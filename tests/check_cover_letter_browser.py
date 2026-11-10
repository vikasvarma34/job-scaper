"""Local browser regression check with every request intercepted (no live database).
Run from the repository root: .venv/bin/python tests/check_cover_letter_browser.py
Uses the existing Playwright dependency and a local Chrome installation.
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as app_module
from flask import render_template
from cover_letter_pdf import create_builder_cover_letter_pdf

letter = json.loads((ROOT / 'cover_letter_builder.json').read_text())
pdf_bytes = create_builder_cover_letter_pdf(letter)
record_id = '123e4567-e89b-12d3-a456-426614174000'
saved_records = {}
pdf_requests = []
pdf_overflow = [False]
save_failure = [False]
delete_failure = [False]
confirm_accept = [True]
save_requests = []
save_version = [0]
count_extra = [0]
resume_source = json.loads((ROOT / 'resume_builder.json').read_text())
resume_copy = json.loads(json.dumps(resume_source))
resume_copy['name'] = 'Disposable Saved Resume'
resume_record = dict(id=record_id, company_name='Resume Test Company', file_name='Resume-Test.pdf', created_at='2026-11-10T00:00:00Z', resume_data=resume_copy)
resume_saves = []
with app_module.app.test_request_context():
    resume_html = render_template('resume_builder.html')

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900}, accept_downloads=True)
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'], origin='https://builder.test')
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('dialog', lambda dialog: dialog.accept() if confirm_accept[0] else dialog.dismiss())

    def route(r):
        url = r.request.url.split('?')[0]
        if '/static/' in url:
            name = url.split('/static/')[1]
            content_types = {'.css': 'text/css', '.js': 'application/javascript', '.svg': 'image/svg+xml'}
            path = ROOT / 'static' / name
            r.fulfill(body=path.read_bytes(), content_type=content_types[path.suffix])
        elif url.endswith('/base'):
            r.fulfill(json={'ok': True, 'cover_letter': letter, 'resume': resume_source})
        elif url.endswith('/api/builder/save'):
            resume_saves.append(r.request.post_data_json)
            r.fulfill(json={'ok': True, 'saved': resume_record})
        elif '/api/builder/resumes/' in url:
            r.fulfill(json={'ok': True, 'resume': resume_record})
        elif url.endswith('/save'):
            payload = r.request.post_data_json
            save_requests.append(payload)
            if save_failure[0]:
                r.fulfill(status=500, json={'ok': False, 'error': 'Database unavailable. Your edits are intact.'})
                return
            source = payload.get('id')
            if source and source not in saved_records:
                r.fulfill(status=404, json={'ok': False, 'code': 'stale_record', 'error': 'Saved letter no longer exists. Your edits are intact. Save again.'})
                return
            company = payload['cover_letter_data']['recipient']['company'].strip()
            matches = [x for x in saved_records.values() if x['id'] != source and x['company_name'] == company and x['file_name'] == payload['file_name']]
            if matches and payload.get('replace_id') != matches[0]['id']:
                r.fulfill(status=409, json={'ok': False, 'code': 'duplicate_document', 'error': 'Letter already exists', 'conflict': matches[0]})
                return
            save_version[0] += 1
            target = payload.get('replace_id') or source or (record_id if not saved_records else '223e4567-e89b-12d3-a456-426614174000')
            action = 'updated' if target in saved_records else 'created'
            saved_records[target] = dict(payload, id=target, company_name=company, updated_at=str(save_version[0]))
            r.fulfill(json={'ok': True, 'saved': saved_records[target], 'action': action})
        elif url.endswith('/saved'):
            r.fulfill(json={'ok': True, 'cover_letters': list(saved_records.values()), 'total': len(saved_records) + count_extra[0]})
        elif '/saved/' in url:
            target = url.rsplit('/', 1)[1]
            if r.request.method == 'DELETE':
                if delete_failure[0]:
                    r.fulfill(status=500, json={'ok': False, 'error': 'Could not delete cover letter. Please try again.'})
                    return
                saved_records.pop(target)
                r.fulfill(json={'ok': True})
            else:
                r.fulfill(json={'ok': True, 'cover_letter': saved_records[target]})
        elif url.endswith('/pdf'):
            pdf_requests.append(r.request.post_data_json)
            if pdf_overflow[0]:
                r.fulfill(status=422, json={'ok': False, 'page_count': 2, 'error': 'This cover letter is 2 pages. Please shorten the content to fit one page. No content has been removed.'})
            else:
                r.fulfill(body=pdf_bytes, content_type='application/pdf')
        elif url.endswith('/resumes'):
            r.fulfill(json={'ok': True, 'resumes': [resume_record], 'total': 137})
        elif url.endswith('/builder/cover-letter'):
            r.fulfill(body=(ROOT / 'templates/cover_letter_builder.html').read_text(), content_type='text/html')
        elif url.endswith('/builder/resume'):
            r.fulfill(body=resume_html, content_type='text/html')
        elif url.endswith('/builder'):
            r.fulfill(body=(ROOT / 'templates/builder_selection.html').read_text(), content_type='text/html')
        else:
            r.abort()

    page.route('**/*', route)
    page.goto('https://builder.test/builder/resume')
    page.locator('#field-name').wait_for()
    def styles(selector, properties):
        return page.locator(selector).first.evaluate('(el, keys) => Object.fromEntries(keys.map(k => [k, getComputedStyle(el)[k]]))', properties)
    reference = {selector: styles(selector, props) for selector, props in [
        ('body', ['backgroundColor', 'fontFamily']),
        ('.app-topbar', ['padding', 'backgroundColor', 'boxShadow']),
        ('.builder-container', ['maxWidth', 'padding']),
        ('.section-card', ['backgroundColor', 'borderRadius', 'boxShadow', 'padding']),
        ('.styled-input', ['backgroundColor', 'borderRadius', 'padding']),
        ('.tab-btn', ['fontSize', 'padding']),
        ('.btn-preview', ['backgroundColor', 'borderRadius', 'boxShadow'])]}
    page.wait_for_function("document.getElementById('history-count').textContent === '137'")
    assert '📁' in page.get_by_role('button', name='Saved Resumes', exact=False).inner_text()
    page.get_by_role('button', name='Saved Resumes', exact=False).click()
    resume_card=page.locator('.history-item').filter(has_text='Resume Test Company')
    resume_card.wait_for()
    before_name=page.locator('#field-name').input_value()
    resume_card.locator('.saved-document-title').click()
    assert page.locator('#field-name').input_value()==before_name
    assert page.locator('#drawer-panel').evaluate("el => el.classList.contains('open')")
    assert resume_card.locator('.saved-document-filename').inner_text()=='Resume-Test.pdf'
    card_reference={selector: resume_card.locator(selector).evaluate('(el) => {const s=getComputedStyle(el);return [s.fontFamily,s.fontSize,s.fontWeight,s.color,s.marginBottom,s.marginTop,s.gap]}') for selector in ['.saved-document-heading','.saved-document-title','.saved-document-filename','.saved-document-actions','.saved-document-load']}
    page.screenshot(path='/tmp/saved-resumes-reference.png')
    resume_card.get_by_text('Load', exact=True).click()
    page.wait_for_function("document.getElementById('field-name').value === 'Disposable Saved Resume'")
    page.locator('#save-db-btn').click()
    page.wait_for_function("document.getElementById('toast').textContent.includes('Saved resume for')")
    assert resume_saves[-1]['id']==record_id
    page.screenshot(path='/tmp/resume-reference-desktop.png')
    page.goto('https://builder.test/builder/cover-letter')
    page.locator('#field-applicant-name').wait_for()
    page.wait_for_function("document.getElementById('saved-count').textContent === '0'")
    assert '📁' in page.locator('#saved-open').inner_text()
    assert page.locator('#font-size').input_value() == '11.5'
    assert page.locator('#font-size option').first.get_attribute('value') == '11'
    assert page.locator('#vertical-margin').input_value() == '0.75'
    assert page.locator('#side-margin').input_value() == '0.75'
    for selector, expected in reference.items():
        assert styles(selector, list(expected)) == expected, selector
    page.locator('#field-recipient-company').fill('First Company')
    assert page.locator('#company').input_value() == 'First Company'
    page.locator('#json-tab').click()
    pasted = json.loads(json.dumps(letter))
    pasted['metadata'] = {'preserve': True}
    pasted['recipient']['company'] = 'Pasted & Company'
    pasted['applicant']['portfolio'] = 'https://example.com'
    pasted['paragraphs'] = ['First paragraph', 'Second paragraph', 'Third paragraph']
    page.locator('#raw').fill(json.dumps(pasted))
    assert page.locator('#company').input_value() == 'Pasted & Company'
    page.locator('#format-json').click()
    page.locator('#copy-json').click()
    page.get_by_text('JSON copied.', exact=True).wait_for()
    assert json.loads(page.evaluate('navigator.clipboard.readText()')) == pasted
    page.screenshot(path='/tmp/cover-builder-json-desktop.png')
    page.locator('#form-tab').click()
    assert page.locator('#field-applicant-portfolio').input_value() == 'https://example.com'
    page.locator('#paragraphs .paragraph').nth(1).get_by_text('Move up', exact=True).click()
    page.locator('#json-tab').click()
    result = json.loads(page.locator('#raw').input_value())
    assert result['paragraphs'] == ['Second paragraph', 'First paragraph', 'Third paragraph']
    assert result['metadata'] == {'preserve': True}
    page.locator('#raw').fill('{broken')
    page.locator('#form-tab').click()
    assert page.locator('#json-view').is_visible()
    assert 'error' in page.locator('#status').get_attribute('class')
    page.locator('#raw').fill(json.dumps(pasted))
    page.locator('#company').fill('Target Change')
    assert json.loads(page.locator('#raw').input_value())['recipient']['company'] == 'Target Change'
    page.locator('#form-tab').click()
    assert page.locator('#field-recipient-company').input_value() == 'Target Change'
    page.locator('#save').click()
    page.locator('#toast').get_by_text('Cover letter saved successfully!', exact=True).wait_for()
    page.wait_for_function("document.getElementById('saved-count').textContent === '1'")
    page.locator('#saved-open').click()
    page.locator('#saved-list .saved-document-title').wait_for()
    cover_card=page.locator('#saved-list .history-item')
    for selector, expected in card_reference.items():
        actual=cover_card.locator(selector).evaluate('(el) => {const s=getComputedStyle(el);return [s.fontFamily,s.fontSize,s.fontWeight,s.color,s.marginBottom,s.marginTop,s.gap]}')
        assert actual==expected,(selector,actual,expected)
    assert cover_card.locator('.saved-document-title').inner_text()=='Target Change'
    assert cover_card.locator('.saved-document-filename').inner_text().endswith('.pdf')
    page.locator('#saved-list').get_by_text('Load', exact=True).click()
    assert page.locator('#field-recipient-company').input_value() == 'Target Change'
    count_extra[0] = 100
    page.locator('#field-subject').fill('Updated application')
    page.locator('#save').click()
    page.locator('#toast').get_by_text('Cover letter updated successfully!', exact=True).wait_for()
    assert saved_records[record_id]['cover_letter_data']['subject'] == 'Updated application'
    page.wait_for_function("document.getElementById('saved-count').textContent === '101'")
    count_extra[0] = 0
    assert save_requests[-1]['id'] == record_id
    original_filename = saved_records[record_id]['file_name']
    # A new editor with the same pair must warn before changing any record.
    page.locator('#reset').click()
    page.locator('#field-recipient-company').fill('Target Change')
    page.locator('#filename').fill(original_filename)
    page.locator('#field-subject').fill('Unsaved duplicate content')
    before_duplicate = json.loads(json.dumps(saved_records))
    page.locator('#save').click()
    page.locator('#duplicate-dialog').wait_for()
    page.screenshot(path='/tmp/cover-letter-duplicate-warning.png')
    page.locator('#duplicate-cancel').click()
    page.get_by_text('Save cancelled. Your edits are intact.', exact=True).wait_for()
    assert saved_records == before_duplicate
    assert page.locator('#field-subject').input_value() == 'Unsaved duplicate content'
    page.locator('#save').click()
    confirm_accept[0] = False
    page.locator('#duplicate-replace').click()
    page.get_by_text('Save cancelled. Your edits are intact.', exact=True).wait_for()
    assert saved_records == before_duplicate
    confirm_accept[0] = True
    page.locator('#save').click()
    page.locator('#duplicate-rename').click()
    page.wait_for_function("document.activeElement === document.getElementById('filename')")
    assert page.locator('#field-subject').input_value() == 'Unsaved duplicate content'
    page.locator('#filename').fill('Different.pdf')
    page.locator('#save').click()
    page.locator('#toast').get_by_text('Cover letter saved successfully!', exact=True).wait_for()
    page.wait_for_function("document.getElementById('saved-count').textContent === '2'")
    assert len(saved_records) == 2
    second_id = [key for key in saved_records if key != record_id][0]
    # Renaming a loaded letter to a different saved letter's pair also warns.
    page.locator('#filename').fill(original_filename)
    page.locator('#save').click()
    page.locator('#duplicate-replace').click()  # Native confirmation is accepted by test handler.
    page.locator('#toast').get_by_text('Cover letter updated successfully!', exact=True).wait_for()
    assert save_requests[-1]['replace_id'] == record_id
    assert save_requests[-1]['id'] == second_id
    assert len(saved_records) == 2  # Original source record remains intact.
    page.locator('#field-subject').fill('After replacement')
    page.locator('#save').click()
    page.locator('#toast').get_by_text('Cover letter updated successfully!', exact=True).wait_for()
    assert save_requests[-1]['id'] == record_id
    # Remove only our disposable second record via the UI.
    page.locator('#saved-open').click()
    delete_failure[0] = True
    page.locator('.saved-item').filter(has_text='Different.pdf').get_by_text('Delete', exact=True).click()
    page.locator('#toast.error').get_by_text('Could not delete cover letter. Please try again.', exact=True).wait_for()
    assert len(saved_records) == 2
    delete_failure[0] = False
    page.locator('.saved-item').filter(has_text='Different.pdf').get_by_text('Delete', exact=True).click()
    page.locator('#toast').get_by_text('Cover letter deleted successfully!', exact=True).wait_for()
    assert page.locator('#toast').evaluate('(el) => el.matches(":popover-open")')
    page.locator('#saved-close').click()
    assert len(saved_records) == 1
    save_failure[0] = True
    page.locator('#field-subject').fill('Keep this unsaved edit')
    page.locator('#save').click()
    page.locator('#toast.error').get_by_text('Database unavailable. Your edits are intact.', exact=True).wait_for()
    assert page.locator('#field-subject').input_value() == 'Keep this unsaved edit'
    save_failure[0] = False
    page.locator('#preview-open').click()
    page.get_by_text('Preview ready.', exact=True).wait_for()
    assert page.locator('#preview').is_visible()
    page.locator('#font-size').select_option('11')
    page.get_by_text('Preview ready.', exact=True).wait_for()
    assert pdf_requests[-1]['font_size'] == 11
    page.screenshot(path='/tmp/cover-builder-preview-desktop.png')
    before_text = page.locator('#paragraphs textarea').evaluate_all('(els) => els.map(el => el.value)')
    pdf_overflow[0] = True
    page.locator('#preview-refresh').click()
    page.locator('#pdf-status').get_by_text('This cover letter is 2 pages.', exact=False).wait_for()
    assert page.locator('#pdf-status').is_visible()
    assert page.locator('#pdf-frame').get_attribute('src') is None
    page.screenshot(path='/tmp/cover-builder-overflow.png')
    page.locator('#preview-close').click()
    downloads = []
    page.on('download', lambda download: downloads.append(download))
    with page.expect_response(lambda response: '/cover-letter/pdf' in response.url) as rejected:
        page.locator('#download').click()
    assert rejected.value.status == 422
    page.locator('#status').get_by_text('This cover letter is 2 pages.', exact=False).wait_for()
    assert not downloads
    assert page.locator('#paragraphs textarea').evaluate_all('(els) => els.map(el => el.value)') == before_text
    assert len(saved_records) == 1
    pdf_overflow[0] = False
    page.locator('#preview-open').click()
    page.get_by_text('Preview ready.', exact=True).wait_for()
    assert page.locator('#pdf-status').is_hidden()
    page.locator('#preview-close').click()
    with page.expect_download() as download:
        page.locator('#download').click()
    assert download.value.suggested_filename.endswith('.pdf')
    assert len(saved_records) == 1
    # Simulate another session deleting only the disposable active record.
    saved_records.pop(record_id)
    page.locator('#save').click()
    page.locator('#toast.error').get_by_text('Saved letter no longer exists. Your edits are intact. Save again.', exact=True).wait_for()
    assert page.locator('#field-subject').input_value() == 'Keep this unsaved edit'
    page.locator('#save').click()
    page.locator('#toast').get_by_text('Cover letter saved successfully!', exact=True).wait_for()
    assert save_requests[-1]['id'] is None
    page.screenshot(path='/tmp/cover-builder-desktop.png')
    page.locator('#saved-open').click()
    page.locator('.saved-item').wait_for()
    page.screenshot(path='/tmp/cover-builder-drawer-desktop.png')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('#saved').bounding_box()['width'] <= 390
    page.screenshot(path='/tmp/cover-builder-drawer-mobile.png')
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.locator('#saved-list').get_by_text('Delete', exact=True).click()
    page.get_by_text('No saved cover letters yet.', exact=True).wait_for()
    assert page.locator('#saved-count').inner_text()=='0'
    assert not saved_records
    page.locator('#saved-close').click()
    for width in [390, 320]:
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), width
        page.screenshot(path=f'/tmp/cover-builder-mobile-{width}.png')
    page.locator('#preview-open').click()
    page.get_by_text('Preview ready.', exact=True).wait_for()
    close_bounds = page.locator('#preview-close').bounding_box()
    assert close_bounds and close_bounds['x'] >= 0 and close_bounds['x'] + close_bounds['width'] <= 320
    assert close_bounds['y'] + close_bounds['height'] <= 844
    page.screenshot(path='/tmp/cover-builder-preview-mobile.png')
    page.locator('#preview-close').click()
    for width in [1280, 390, 320]:
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto('https://builder.test/builder')
        assert page.locator('.selection-card').count() == 2
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), width
        page.screenshot(path=f'/tmp/builder-selection-{width}.png')
    assert not errors, errors
    browser.close()
    print('Browser checks passed: resume style parity; desktop/mobile selection, editor, preview and drawer; JSON/form round trips; preview/download; mocked save/load/update/delete. No live database requests.')
