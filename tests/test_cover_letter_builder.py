import io
import json
import unittest
from pathlib import Path
from unittest import mock
import pdfplumber
import app as app_module
from cover_letter_model import validate_cover_letter
from cover_letter_pdf import create_builder_cover_letter_pdf, create_cover_letter_pdf, CoverLetterPageLimitError

ROOT = Path(__file__).resolve().parents[1]
from supabase_utils import count_saved_documents as REAL_COUNT_SAVED_DOCUMENTS

RECORD_ID = '123e4567-e89b-12d3-a456-426614174000'


class CoverLetterBuilderTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.letter = json.loads((ROOT / 'cover_letter_builder.json').read_text())
        self.letter['recipient']['company'] = 'Example & Company'
        self.payload = dict(cover_letter_data=self.letter, file_name='Application.pdf')
        count = mock.patch('supabase_utils.count_saved_documents', return_value=137)
        self.count_documents = count.start()
        self.addCleanup(count.stop)
        conflicts = mock.patch('supabase_utils.find_builder_cover_letter_conflicts', return_value=[])
        self.conflict_patcher = conflicts
        self.conflicts = conflicts.start()
        self.addCleanup(conflicts.stop)
        get = mock.patch('supabase_utils.get_builder_cover_letter', return_value={'id': RECORD_ID})
        self.get_record = get.start()
        self.addCleanup(get.stop)


    def test_routes_and_default(self):
        for route, expected in [('/builder', b'/builder/resume'), ('/builder', b'/builder/cover-letter'),
                                ('/builder/resume', b'Preview Resume'), ('/builder/cover-letter', b'Raw JSON')]:
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, response.data)
        response = self.client.get('/api/builder/cover-letter/base')
        self.assertEqual(response.status_code, 200)
        validate_cover_letter(response.json['cover_letter'])
        self.assertIn('no-store', response.headers['Cache-Control'])

    @mock.patch('app._fetch_dashboard_data', return_value={'stats': {}, 'jobs': [], 'jobs_error': None})
    def test_home_navigation_label(self, dashboard):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Resume or Cover Letter Builder', response.data)

    def test_validation_and_unknown_fields_preserved(self):
        self.letter['extra'] = {'notes': 'keep me'}
        self.letter['applicant']['custom'] = 'preserved'
        self.assertEqual(validate_cover_letter(self.letter), self.letter)
        for change in [{'paragraphs': 'wrong'}, {'paragraphs': []}, {'date': '2026-02-30'}, {'signature': None}, {'applicant': []}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_cover_letter(dict(self.letter, **change))
        for route in ('save', 'pdf'):
            response = self.client.post('/api/builder/cover-letter/'+route, json={'cover_letter_data': {'invalid': True}})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.client.post('/api/builder/cover-letter/'+route, json=[]).status_code, 400)

    @mock.patch('supabase_utils.save_builder_cover_letter')
    def test_pdf_preview_download_no_save(self, save):
        for preview in ('', '?preview=true'):
            response = self.client.post('/api/builder/cover-letter/pdf'+preview, json=self.payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'application/pdf')
            self.assertIn('inline' if preview else 'attachment', response.headers['Content-Disposition'])
            with pdfplumber.open(io.BytesIO(response.data)) as pdf:
                text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
                self.assertIn('Example & Company', text)
                self.assertIn('Dear Hiring Manager', text)
        save.assert_not_called()
        self.assertEqual(self.client.post('/api/builder/cover-letter/pdf', json=dict(self.payload, font_size=100)).status_code, 400)
        self.assertEqual(self.client.post('/api/builder/cover-letter/pdf', json=dict(self.payload, side_margin='nan')).status_code, 400)

    def test_special_characters_and_long_paragraph_rejection(self):
        self.letter['paragraphs'] = ['Café & APIs <platform> — engineering.']
        with pdfplumber.open(io.BytesIO(create_builder_cover_letter_pdf(self.letter))) as pdf:
            self.assertEqual(len(pdf.pages), 1)
            self.assertIn('Café & APIs <platform> — engineering.', pdf.pages[0].extract_text())
        self.letter['paragraphs'] *= 800
        before = json.dumps(self.letter)
        with self.assertRaises(CoverLetterPageLimitError) as error:
            create_builder_cover_letter_pdf(self.letter)
        self.assertGreater(error.exception.page_count, 1)
        self.assertIn('No content has been removed', str(error.exception))
        self.assertEqual(json.dumps(self.letter), before)

    @mock.patch('supabase_utils.save_builder_cover_letter')
    def test_two_page_overflow_preview_and_download(self, save):
        self.letter['paragraphs'] = ['Software engineering and full stack development experience. ' * 55]
        before = json.dumps(self.payload)
        for suffix in ('', '?preview=true'):
            response = self.client.post('/api/builder/cover-letter/pdf'+suffix, json=self.payload)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.mimetype, 'application/json')
            self.assertEqual(response.json['page_count'], 2)
            self.assertIn('This cover letter is 2 pages.', response.json['error'])
        save.assert_not_called()
        self.assertEqual(json.dumps(self.payload), before)

    def test_a4_embedded_arimo_without_system_fonts(self):
        from pdfminer.pdftypes import resolve1
        from reportlab.lib.pagesizes import A4
        import builtins
        original_open = builtins.open
        def no_system_fonts(path, *args, **kwargs):
            if str(path).startswith(('/System/Library/Fonts', '/Library/Fonts')):
                raise FileNotFoundError('System fonts intentionally unavailable')
            return original_open(path, *args, **kwargs)
        with mock.patch('builtins.open', side_effect=no_system_fonts), mock.patch(
                'cover_letter_pdf._register_arial_fonts', side_effect=AssertionError('Legacy font loader must not be used')):
            data = create_builder_cover_letter_pdf(self.letter)
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page = pdf.pages[0]
            self.assertAlmostEqual(page.width, A4[0], places=3)
            self.assertAlmostEqual(page.height, A4[1], places=3)
            self.assertEqual(len(pdf.pages), 1)
            self.assertIn('Dear Hiring Manager', page.extract_text())
            used = {char['fontname'] for char in page.chars}
            self.assertTrue(all('Arimo' in font for font in used), used)
            fonts = resolve1(resolve1(page.page_obj.resources)['Font'])
            embedded = set()
            for ref in fonts.values():
                font = resolve1(ref)
                if 'Arimo' in str(font.get('BaseFont')):
                    self.assertIn('FontFile2', resolve1(font['FontDescriptor']))
                    embedded.add(str(font['BaseFont']))
            self.assertEqual(len(embedded), 2)
            sizes = {round(c['size'], 3) for c in page.chars}
            self.assertEqual(sizes, {11.5, 16})

    def test_exact_defaults_and_readable_size_limit(self):
        import cover_letter_pdf as renderer
        from reportlab.lib.pagesizes import A4
        styles = {}
        original_style = renderer.ParagraphStyle
        def capture_style(name, **kwargs):
            result = original_style(name, **kwargs)
            styles[name] = result
            return result
        with mock.patch.object(renderer, 'SimpleDocTemplate', wraps=renderer.SimpleDocTemplate) as doc, mock.patch.object(
                renderer, 'ParagraphStyle', side_effect=capture_style):
            create_builder_cover_letter_pdf(self.letter)
        config = doc.call_args.kwargs
        self.assertEqual(config['pagesize'], A4)
        for margin in ('topMargin', 'bottomMargin', 'leftMargin', 'rightMargin'):
            self.assertEqual(config[margin], 54)
        for name in ('LetterBody', 'LetterContact', 'LetterSubject'):
            self.assertEqual(styles[name].fontSize, 11.5)
            self.assertAlmostEqual(styles[name].leading, 14.03)
            self.assertEqual(styles[name].alignment, 0)
        self.assertEqual(styles['LetterBody'].spaceAfter, 12)
        self.assertEqual(styles['LetterHeader'].fontSize, 16)
        self.assertEqual(styles['LetterHeader'].spaceAfter, 8)
        self.assertEqual(styles['LetterContact'].spaceAfter, 2)
        for name, before, after in [('LetterContactLast', 0, 16), ('LetterDate', 16, 16),
                                     ('LetterRecipient', 0, 18), ('LetterSubject', 12, 12),
                                     ('LetterGreeting', 0, 12), ('LetterClosing', 16, 8)]:
            self.assertEqual(styles[name].spaceBefore, before)
            self.assertEqual(styles[name].spaceAfter, after)
        self.assertEqual(styles['LetterSignature'].spaceAfter, 0)
        self.assertEqual(self.client.post('/api/builder/cover-letter/pdf', json=dict(self.payload, font_size=10)).status_code, 400)
        with pdfplumber.open(io.BytesIO(create_builder_cover_letter_pdf(self.letter, font_size=12, side_margin=1))) as pdf:
            self.assertIn(12, {round(c['size']) for c in pdf.pages[0].chars})

    def test_contact_block_and_major_sections_have_distinct_gaps(self):
        import re
        self.letter['applicant'].update(email='vikas@example.com', linkedin='https://linkedin.com/in/example')
        self.letter['recipient']['address'] = 'Hyderabad, India'
        self.letter['date'] = '2026-11-10'
        with pdfplumber.open(io.BytesIO(create_builder_cover_letter_pdf(self.letter))) as pdf:
            page = pdf.pages[0]
            def bounds(text):
                matches = page.search(re.escape(text))
                self.assertTrue(matches, text)
                return matches[0]
            contact = bounds('vikas@example.com')
            linkedin = bounds('https://linkedin.com/in/example')
            date = bounds('November 10, 2026')
            recipient = bounds('Hyderabad, India')
            subject = bounds(self.letter['subject'])
            greeting = bounds(self.letter['greeting'])
            signature = page.search(re.escape(self.letter['signature']))[-1]
            closing = bounds(self.letter['closing'])
            self.assertLess(linkedin['top'] - contact['bottom'], 6)
            self.assertGreater(date['top'] - linkedin['bottom'], 16)
            self.assertGreater(subject['top'] - recipient['bottom'], 18)
            self.assertGreater(greeting['top'] - subject['bottom'], 12)
            self.assertGreater(signature['top'] - closing['bottom'], 8)
            self.assertEqual(len(pdf.pages), 1)

    def test_preview_download_layout_consistency(self):
        layouts = []
        for suffix in ('', '?preview=true'):
            response = self.client.post('/api/builder/cover-letter/pdf'+suffix, json=self.payload)
            self.assertEqual(response.status_code, 200)
            with pdfplumber.open(io.BytesIO(response.data)) as pdf:
                layouts.append([(p.width, p.height, [(c['text'], c['fontname'], c['size'], c['x0'], c['top']) for c in p.chars]) for p in pdf.pages])
        self.assertEqual(layouts[0], layouts[1])

    def test_legacy_pdf_signature_and_defaults(self):
        pdf_bytes = create_cover_letter_pdf(applicant_name='Test Applicant', email='test@example.com',
            phone='123', location='Hyderabad', cover_letter_text='Dear Team,\n\nLegacy body & details.\n\nSincerely,\nTest Applicant')
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            self.assertEqual(len(pdf.pages), 1)
            text = pdf.pages[0].extract_text()
            self.assertIn('Legacy body & details.', text)
            self.assertIn('Sincerely,', text)

    @mock.patch('supabase_utils.save_builder_cover_letter')
    def test_save_and_update(self, save):
        save.return_value = dict(id=RECORD_ID, **self.payload)
        for record_id in (None, RECORD_ID):
            response = self.client.post('/api/builder/cover-letter/save', json=dict(self.payload, id=record_id, company_name='Ignored stale company'))
            self.assertEqual(response.status_code, 200)
            save.assert_called_with('Example & Company', 'Application.pdf', self.letter, record_id)
            self.assertEqual(response.json['action'], 'updated' if record_id else 'created')
        self.letter['recipient']['company'] = ''
        self.assertEqual(self.client.post('/api/builder/cover-letter/save', json=self.payload).status_code, 400)
        save.side_effect = RuntimeError('test failure')
        self.letter['recipient']['company'] = 'Company'
        self.assertEqual(self.client.post('/api/builder/cover-letter/save', json=self.payload).status_code, 500)

    @mock.patch('supabase_utils.list_builder_cover_letters')
    @mock.patch('supabase_utils.get_builder_cover_letter')
    @mock.patch('supabase_utils.delete_builder_cover_letter')
    def test_list_load_delete(self, delete, get, listing):
        listing.return_value = [dict(id=RECORD_ID, company_name='Company')]
        get.return_value = dict(id=RECORD_ID, **self.payload)
        delete.return_value = True
        response = self.client.get('/api/builder/cover-letter/saved')
        self.assertEqual(len(response.json['cover_letters']), 1)
        self.assertEqual(response.json['total'], 137)
        url = '/api/builder/cover-letter/saved/'+RECORD_ID
        self.assertEqual(self.client.get(url).json['cover_letter']['cover_letter_data'], self.letter)
        self.assertEqual(self.client.delete(url).status_code, 200)
        get.return_value = None
        delete.return_value = False
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.get('/api/builder/cover-letter/saved/bad-id').status_code, 400)
        listing.side_effect = RuntimeError('test failure')
        self.assertEqual(self.client.get('/api/builder/cover-letter/saved').status_code, 500)

    @mock.patch('supabase_utils.supabase')
    def test_exact_total_count_is_independent_of_history_limit(self, db):
        import supabase_utils as su
        # Invoke the real helper despite the per-test network guard.
        with mock.patch('supabase_utils.count_saved_documents', wraps=REAL_COUNT_SAVED_DOCUMENTS) as count:
            db.table.return_value.select.return_value.execute.return_value.count = 137
            self.assertEqual(count('saved_cover_letters'), 137)
            db.table.return_value.select.assert_called_with('id', count='exact', head=True)
            db.table.return_value.limit.assert_not_called()

    @mock.patch('supabase_utils.supabase')
    def test_database_helpers_insert_update(self, db):
        import supabase_utils as su
        table = db.table.return_value
        table.insert.return_value.execute.return_value.data = [{'id': RECORD_ID}]
        table.update.return_value.eq.return_value.execute.return_value.data = [{'id': RECORD_ID}]
        self.assertEqual(su.save_builder_cover_letter('Company', 'File.pdf', self.letter)['id'], RECORD_ID)
        su.save_builder_cover_letter('Company', 'File.pdf', self.letter, RECORD_ID)
        table.update.return_value.eq.assert_called_once_with('id', RECORD_ID)
        db.table.assert_called_with('saved_cover_letters')

    @mock.patch('supabase_utils.save_builder_cover_letter')
    @mock.patch('supabase_utils.replace_builder_cover_letter')
    def test_duplicate_requires_explicit_replacement(self, replace, save):
        other_id = '223e4567-e89b-12d3-a456-426614174000'
        conflict = dict(id=other_id, company_name='Example & Company', file_name='Application.pdf', updated_at='2026-11-10T10:00:00Z')
        self.conflicts.return_value = [conflict]
        for source in (None, RECORD_ID):
            request = dict(self.payload, id=source)
            response = self.client.post('/api/builder/cover-letter/save', json=request)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json['code'], 'duplicate_document')
            self.assertEqual(response.json['conflict'], conflict)
            save.assert_not_called()
            replace.assert_not_called()
            replace.return_value = dict(id=other_id)
            response = self.client.post('/api/builder/cover-letter/save', json=dict(request, replace_id=other_id, replace_updated_at=conflict['updated_at']))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['action'], 'updated')
            replace.assert_called_with('Example & Company', 'Application.pdf', self.letter, other_id, conflict['updated_at'])
            replace.reset_mock()
        # A target edited/deleted while the dialog was open must not be overwritten.
        response = self.client.post('/api/builder/cover-letter/save', json=dict(self.payload, replace_id=other_id, replace_updated_at='older'))
        self.assertEqual(response.status_code, 409)
        replace.assert_not_called()
        replace.return_value = None
        response = self.client.post('/api/builder/cover-letter/save', json=dict(self.payload, replace_id=other_id, replace_updated_at=conflict['updated_at']))
        self.assertEqual(response.json['code'], 'replacement_changed')

    @mock.patch('supabase_utils.save_builder_cover_letter')
    def test_stale_ids_and_database_errors_never_report_success(self, save):
        self.get_record.return_value = None
        response = self.client.post('/api/builder/cover-letter/save', json=dict(self.payload, id=RECORD_ID))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json['code'], 'stale_record')
        save.assert_not_called()
        self.conflicts.side_effect = RuntimeError('database unavailable')
        response = self.client.post('/api/builder/cover-letter/save', json=self.payload)
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json['ok'])
        save.assert_not_called()
        self.conflicts.side_effect = None
        save.return_value = None
        self.assertEqual(self.client.post('/api/builder/cover-letter/save', json=self.payload).status_code, 500)
        self.conflicts.return_value = [{'id': 'a'}, {'id': 'b'}]
        self.assertEqual(self.client.post('/api/builder/cover-letter/save', json=self.payload).json['code'], 'ambiguous_duplicate')

    @mock.patch('supabase_utils.save_builder_cover_letter')
    def test_concurrent_unique_conflict_and_disappearing_update(self, save):
        class UniqueConflict(Exception):
            code = '23505'
        save.side_effect = UniqueConflict()
        response = self.client.post('/api/builder/cover-letter/save', json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'duplicate_race')
        save.side_effect = None
        save.return_value = None
        response = self.client.post('/api/builder/cover-letter/save', json=dict(self.payload, id=RECORD_ID))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json['code'], 'stale_record')

    @mock.patch('supabase_utils.delete_builder_cover_letter', side_effect=RuntimeError('database unavailable'))
    def test_delete_failure_is_not_success(self, delete):
        response = self.client.delete('/api/builder/cover-letter/saved/'+RECORD_ID)
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json['ok'])

    @mock.patch('supabase_utils.supabase')
    def test_conflict_query_is_not_limited_to_latest_history(self, db):
        import supabase_utils as su
        query = db.table.return_value
        query.select.return_value = query
        query.eq.return_value = query
        query.neq.return_value = query
        query.limit.return_value = query
        query.execute.return_value.data = [{'id': RECORD_ID}]
        self.conflict_patcher.stop()
        self.assertEqual(su.find_builder_cover_letter_conflicts('Company', 'File.pdf', 'other'), [{'id': RECORD_ID}])
        query.eq.assert_any_call('company_name', 'Company')
        query.eq.assert_any_call('file_name', 'File.pdf')
        query.neq.assert_called_once_with('id', 'other')
        query.order.assert_not_called()
        query.limit.assert_called_once_with(2)
        su.replace_builder_cover_letter('Company', 'File.pdf', self.letter, RECORD_ID, 'version')
        # Replacement guards live in a single UPDATE, not a separate blind overwrite.
        query.update.return_value.eq.assert_called_once_with('id', RECORD_ID)


if __name__ == '__main__':
    unittest.main()
