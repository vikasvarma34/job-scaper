"""
Universal LLM Client using LiteLLM.

Provides a unified interface for 400+ LLMs with built-in rate limiting,
exponential backoff, and daily budget tracking.

Usage:
    from llm_client import primary_client

    response = primary_client.generate_content(
        prompt="Hello!",
        system_prompt="You are a helpful assistant.",
        temperature=0.2,
        response_format=MyPydanticModel  # Optional structured output
    )
"""

import os
import time
import random
import logging
import threading
from typing import Optional, Any, Type

import litellm
from pydantic import BaseModel

import config

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging unless DEBUG is set
litellm.suppress_debug_info = True
if os.environ.get("LLM_DEBUG", "").lower() == "true":
    litellm.set_verbose = True
else:
    for logger_name in ("LiteLLM", "litellm"):
        third_party_logger = logging.getLogger(logger_name)
        third_party_logger.setLevel(logging.WARNING)
        third_party_logger.handlers.clear()
        third_party_logger.propagate = True


class RateLimiter:
    """Token-bucket rate limiter for requests per minute."""

    def __init__(self, max_rpm: int):
        self.max_rpm = max_rpm
        self.tokens = max_rpm
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self):
        """Block until a request token is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                # Refill tokens based on elapsed time
                refill = elapsed * (self.max_rpm / 60.0)
                self.tokens = min(self.max_rpm, self.tokens + refill)
                self.last_refill = now

                if self.tokens >= 1:
                    self.tokens -= 1
                    return

            # Wait a bit before retrying
            time.sleep(0.5)


class LLMClient:
    """
    Universal LLM client powered by LiteLLM.

    Wraps litellm.completion() with rate limiting, exponential backoff,
    and daily budget tracking.
    """

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        max_rpm: int = 10,
        max_retries: int = 3,
        retry_base_delay: int = 10,
        daily_budget: int = 0,
        request_delay: float = 0,
    ):
        """
        Initialize the LLM client.

        Args:
            model: LiteLLM model string (e.g., "gemini/gemini-2.5-flash-lite")
            api_key: API key for the provider (auto-detected from env if not set)
            max_rpm: Maximum requests per minute
            max_retries: Max retries on rate-limit errors
            retry_base_delay: Base delay in seconds for exponential backoff
            daily_budget: Max requests per day (0 = unlimited)
            request_delay: Fixed delay between requests in seconds
        """
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.daily_budget = daily_budget
        self.request_delay = request_delay
        self.rate_limiter = RateLimiter(max_rpm)

        # Daily budget tracking
        self._daily_count = 0
        self._daily_reset_time = time.time()

        # Set API key in environment if provided (LiteLLM reads from env)
        if api_key:
            self._set_api_key_env(api_key)

    def _set_api_key_env(self, api_key: str):
        """Set the appropriate environment variable based on the model provider."""
        provider = self.model.split("/")[0] if "/" in self.model else self.model.lower()
        if provider == "google":
            provider = "gemini"
        env_var_map = {
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        env_var = env_var_map.get(provider)
        if env_var and not os.environ.get(env_var):
            os.environ[env_var] = api_key

    def _check_daily_budget(self):
        """Check if daily request budget is exceeded. Resets at midnight."""
        if self.daily_budget <= 0:
            return  # Unlimited

        # Reset counter if 24 hours have passed
        if time.time() - self._daily_reset_time > 86400:
            self._daily_count = 0
            self._daily_reset_time = time.time()

        if self._daily_count >= self.daily_budget:
            raise RuntimeError(
                f"Daily LLM request budget exceeded ({self.daily_budget} requests). "
                f"Increase LLM_DAILY_REQUEST_BUDGET or wait for reset."
            )

    def generate_content(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 1,
        response_format: Optional[Type[BaseModel]] = None,
        model_override: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        verbosity: Optional[str] = None,
    ) -> str:
        """
        Generate content using the configured LLM.

        Args:
            prompt: The user prompt/message
            system_prompt: Optional system instruction
            temperature: Temperature for generation (0.0-1.0)
            response_format: Optional Pydantic model for structured JSON output
            model_override: Override the default model for this call
            reasoning_effort: Optional reasoning effort for supported models
            verbosity: Optional verbosity for supported models

        Returns:
            The generated text content as a string

        Raises:
            RuntimeError: If daily budget is exceeded
            Exception: If all retries are exhausted
        """
        self._check_daily_budget()

        model = model_override or self.model
        normalized_temperature = temperature
        normalized_reasoning_effort = reasoning_effort

        # OpenAI GPT-5 reasoning calls currently require temperature=1.
        # Keep legacy/non-reasoning calls unchanged, but normalize ATS planner-style
        # calls so they don't fail and fall back to the old flow.
        if (
            model.lower().startswith("openai/gpt-5")
            and normalized_reasoning_effort not in (None, "", "none")
            and normalized_temperature != 1
        ):
            logger.info(
                "Normalizing temperature to 1 for OpenAI GPT-5 reasoning call "
                f"(requested {normalized_temperature})."
            )
            normalized_temperature = 1

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Build base kwargs for litellm.completion
        base_kwargs = {
            "messages": messages,
            "temperature": normalized_temperature,
        }

        # Add API key if set
        if self.api_key:
            base_kwargs["api_key"] = self.api_key
        if self.api_base:
            base_kwargs["api_base"] = self.api_base

        # Add structured output (Pydantic model)
        if response_format is not None:
            base_kwargs["response_format"] = response_format
        if normalized_reasoning_effort is not None:
            base_kwargs["reasoning_effort"] = normalized_reasoning_effort
        if verbosity is not None:
            base_kwargs["verbosity"] = verbosity

        last_exception = None

        is_dynamic_gemini = model.lower() in ("gemini", "google")
        gemini_pool = [
            "gemini/gemini-3.1-flash-lite-preview",
            "gemini/gemini-3-flash-preview",
            "gemini/gemini-2.5-flash",
            "gemini/gemini-2.5-flash-lite",
        ]
        pool_index = 0
        
        # Ensure we retry enough times to try all models in the pool if dynamic
        max_attempts = max(self.max_retries + 1, len(gemini_pool)) if is_dynamic_gemini else self.max_retries + 1

        for attempt in range(max_attempts):
            try:
                # Rate limiting
                self.rate_limiter.acquire()

                # Fixed inter-request delay
                if self.request_delay > 0 and attempt == 0:
                    time.sleep(self.request_delay)
                    
                current_model = gemini_pool[pool_index % len(gemini_pool)] if is_dynamic_gemini else model
                kwargs = base_kwargs.copy()
                kwargs["model"] = current_model

                logger.debug(f"LLM request attempt {attempt + 1}/{max_attempts} to {current_model}")
                response = litellm.completion(**kwargs)

                # Track daily usage
                self._daily_count += 1

                # Extract text from response
                content = response.choices[0].message.content
                if content:
                    return content.strip()
                else:
                    logger.warning(f"LLM returned empty content on model {current_model} (attempt {attempt + 1}/{max_attempts}).")
                    if attempt < max_attempts - 1:
                        delay = random.uniform(0.8, 2.0)
                        logger.warning(f"Retrying after empty content in {delay:.1f}s...")
                        time.sleep(delay)
                        continue
                    return ""

            except Exception as e:
                last_exception = e
                error_str = str(e).lower()

                # Check if it's a rate limit / quota error
                is_rate_limit = any(keyword in error_str for keyword in [
                    "429", "rate_limit", "rate limit", "resource_exhausted",
                    "quota", "too many requests", "retry"
                ])

                if is_rate_limit and attempt < max_attempts - 1:
                    if is_dynamic_gemini:
                        pool_index += 1
                        delay = random.uniform(1, 4) # Short delay when switching models
                        logger.warning(
                            f"Rate limit hit for {current_model}. Switching to next pool model... "
                            f"(attempt {attempt + 1}/{max_attempts}). Retrying in {delay:.1f}s. Error: {e}"
                        )
                    else:
                        # Exponential backoff with jitter
                        delay = self.retry_base_delay * (2 ** attempt) + random.uniform(0, 5)
                        logger.warning(
                            f"Rate limit hit (attempt {attempt + 1}/{max_attempts}). "
                            f"Retrying in {delay:.1f}s... Error: {e}"
                        )
                    time.sleep(delay)
                    continue
                elif not is_rate_limit:
                    # Non-rate-limit error — don't retry
                    logger.error(f"LLM API error (non-retryable) on model {current_model if 'current_model' in locals() else model}: {e}")
                    raise

        # All retries exhausted
        failed_model = current_model if 'current_model' in locals() else model
        logger.error(f"All {max_attempts} attempts failed for model {failed_model}")
        raise last_exception


def _create_client(
    model: str,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> LLMClient:
    """Create an LLMClient instance with config-based defaults."""
    return LLMClient(
        model=model,
        api_key=api_key,
        api_base=api_base,
        max_rpm=config.LLM_MAX_RPM,
        max_retries=config.LLM_MAX_RETRIES,
        retry_base_delay=config.LLM_RETRY_BASE_DELAY,
        daily_budget=config.LLM_DAILY_REQUEST_BUDGET,
        request_delay=config.LLM_REQUEST_DELAY_SECONDS,
    )


# --- Global Client Instances ---

# Primary client (used by score_jobs, resume_parser, custom_resume_generator)
primary_client = _create_client(
    model=config.LLM_MODEL,
    api_key=config.LLM_API_KEY,
)

# Dedicated scoring client (can be routed to a different provider/model like Sarvam)
scoring_client = _create_client(
    model=config.SCORING_LLM_MODEL,
    api_key=config.SCORING_LLM_API_KEY,
    api_base=config.SCORING_LLM_API_BASE,
)
