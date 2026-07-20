import json
import logging
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import openai
from babeldoc.utils.atomic_integer import AtomicInteger
from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.translator.base_rate_limiter import BaseRateLimiter
from pdf2zh_next.translator.base_translator import BaseTranslator
from pdf_runtime.config import env_enabled
from pdf_runtime.config import env_float
from pdf_runtime.config import env_int
from pdf_runtime.config import env_text

# PDFMathTranslate suppresses logger names containing "openai"; keep provider
# timing visible under the RFQ engine namespace without logging payload text.
logger = logging.getLogger("rfq_pdf_translation.batch")


@dataclass
class _BatchItem:
    item_id: str
    text: str
    done: threading.Event
    result: str | None = None
    error: Exception | None = None


class OpenAITranslator(BaseTranslator):
    """Batch short PDF segments into normal OpenAI-compatible chat requests."""

    name = "openai"

    def __init__(
        self,
        settings: SettingsModel,
        rate_limiter: BaseRateLimiter,
    ):
        super().__init__(settings, rate_limiter)
        engine = settings.translate_engine_settings
        self.model = engine.openai_model
        self.repair_model = env_text(
            "B_PDF_TRANSLATION_REPAIR_MODEL", self.model
        ) or self.model
        self.batch_size = env_int("B_PDF_TRANSLATION_BATCH_SIZE", 20, maximum=32)
        self.max_chars = env_int(
            "B_PDF_TRANSLATION_BATCH_MAX_CHARS", 6000, maximum=12000
        )
        self.flush_ms = env_int(
            "B_PDF_TRANSLATION_BATCH_FLUSH_MS", 100, maximum=2000
        )
        self.timeout = float(
            engine.openai_timeout
            or env_float("B_PDF_TRANSLATION_BATCH_TIMEOUT", 90.0, 1.0)
        )
        if self.timeout > 300:
            raise ValueError("B_PDF_TRANSLATION_BATCH_TIMEOUT 不能大于 300")
        self.max_retries = env_int(
            "B_PDF_TRANSLATION_BATCH_RETRIES", 4, maximum=8
        )
        self.request_workers = env_int(
            "B_PDF_TRANSLATION_BATCH_REQUEST_WORKERS", 4, maximum=8
        )
        self.custom_system_prompt = settings.translation.custom_system_prompt or ""
        api_key = env_text("VECTOR_ENGINE_API_KEY")
        if not api_key:
            raise RuntimeError("未配置 VECTOR_ENGINE_API_KEY")
        self.client = openai.OpenAI(
            base_url=engine.openai_base_url,
            api_key=api_key,
            timeout=self.timeout,
            max_retries=0,
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=max(8, self.request_workers * 4),
                    max_keepalive_connections=max(4, self.request_workers * 2),
                )
            ),
        )
        self.disable_thinking = env_enabled(
            "B_PDF_TRANSLATION_DISABLE_THINKING", True
        )
        self.force_simple_path = env_enabled(
            "B_PDF_TRANSLATION_FORCE_SIMPLE_PATH", False
        )

        self.add_cache_impact_parameters("model", self.model)
        self.add_cache_impact_parameters("repair_model", self.repair_model)
        self.add_cache_impact_parameters("batch_prompt", "rfq-openai-compatible-batch-v1")
        self.add_cache_impact_parameters("batch_size", self.batch_size)
        self.add_cache_impact_parameters("max_chars", self.max_chars)
        self.add_cache_impact_parameters("custom_system_prompt", self.custom_system_prompt)
        if self.disable_thinking:
            self.add_cache_impact_parameters("thinking", "disabled")
        if self.force_simple_path:
            self.add_cache_impact_parameters("translation_path", "paragraph_batch")

        self.token_count = AtomicInteger()
        self.prompt_token_count = AtomicInteger()
        self.completion_token_count = AtomicInteger()
        self.cache_hit_prompt_token_count = AtomicInteger()
        self.batch_request_count = AtomicInteger()
        self._queue: queue.Queue[_BatchItem] = queue.Queue()
        self._counter = AtomicInteger()
        self._closed = False
        self._request_executor = ThreadPoolExecutor(
            max_workers=self.request_workers,
            thread_name_prefix="rfq-oaicbatch-request",
        )
        self._aggregator = threading.Thread(
            target=self._worker_loop,
            name="rfq-oaicbatch-aggregator",
            daemon=True,
        )
        self._aggregator.start()

    def translate(self, text, ignore_cache=False, rate_limit_params: dict = None):
        self.translate_call_count += 1
        if not (self.ignore_cache or ignore_cache):
            try:
                cached = self.cache.get(text)
                if cached is not None:
                    self.translate_cache_call_count += 1
                    return cached
            except Exception as exc:
                logger.debug("Batch cache lookup failed and was ignored: %s", exc)

        item = _BatchItem(
            item_id=f"i{self._counter.inc()}",
            text=text,
            done=threading.Event(),
        )
        self._queue.put(item)
        if not item.done.wait((self.timeout * self.max_retries) + 30):
            raise TimeoutError("OpenAI-compatible batched translation timed out")
        if item.error:
            raise item.error
        translation = item.result if item.result is not None else text
        if not (self.ignore_cache or ignore_cache):
            self.cache.set(text, translation)
        return translation

    def do_translate(self, text, rate_limit_params: dict = None) -> str:
        item = _BatchItem(
            item_id=f"i{self._counter.inc()}",
            text=text,
            done=threading.Event(),
        )
        self._process_batch([item])
        if item.error:
            raise item.error
        return item.result if item.result is not None else text

    def do_llm_translate(self, text, rate_limit_params: dict = None):
        if self.force_simple_path:
            raise NotImplementedError("RFQ paragraph batching uses the stable translator path")
        if text is None:
            return None
        started = time.perf_counter()
        messages = []
        if self.custom_system_prompt.strip():
            messages.append(
                {"role": "system", "content": self.custom_system_prompt.strip()}
            )
        messages.append({"role": "user", "content": text})
        response = self._request(
            messages=messages,
            request_json_mode=bool(
                rate_limit_params and rate_limit_params.get("request_json_mode", False)
            ),
        )
        logger.info(
            "RFQLLM chars=%s seconds=%.3f model=%s",
            len(text),
            time.perf_counter() - started,
            self.model,
        )
        content = self._remove_cot_content(response.choices[0].message.content.strip())
        return self._repair_residual_cyrillic(content)

    @staticmethod
    def _has_actionable_cyrillic(text: str) -> bool:
        """Ignore protected names/codes and detect untranslated Russian prose."""

        cleaned = re.sub(r"(?iu)\bООО\b", " ", text)
        cleaned = re.sub(
            r"(?iu)\b(?=[\w./-]*\d)[\w./-]*[\u0400-\u04ff][\w./-]*\b",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"(?u)\b[А-ЯЁ]{1,3}\b", " ", cleaned)
        return re.search(r"(?iu)\b[\u0400-\u04ff]{3,}\b", cleaned) is not None

    def _repair_residual_cyrillic(self, content: str) -> str:
        """Repair only rows where the low-cost model left Russian prose behind."""

        try:
            payload = json.loads(self._extract_json(content))
        except (TypeError, ValueError, json.JSONDecodeError):
            return content

        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get("translations"), list):
            rows = payload["translations"]
        elif isinstance(payload, dict):
            rows = [payload]
        else:
            return content

        row_map = {
            str(row.get("id", index)): row
            for index, row in enumerate(rows)
            if isinstance(row, dict) and isinstance(row.get("output"), str)
        }
        repaired = self._repair_translation_map(
            {item_id: row["output"] for item_id, row in row_map.items()}
        )
        for item_id, translated in repaired.items():
            row_map[item_id]["output"] = translated
        return json.dumps(payload, ensure_ascii=False)

    def _repair_translation_map(self, translations: dict[str, str]) -> dict[str, str]:
        suspects = {
            item_id: text
            for item_id, text in translations.items()
            if self._has_actionable_cyrillic(text)
        }
        if not suspects:
            return translations

        repair_payload = {
            "items": [
                {"id": item_id, "text": text}
                for item_id, text in suspects.items()
            ]
        }
        started = time.perf_counter()
        try:
            response = self._request(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You repair technical RFQ translations. Translate only remaining "
                            "Russian/Cyrillic natural-language prose into Simplified Chinese and "
                            "remove that Russian source text from the repaired result. "
                            "Keep existing Chinese and English unchanged. Preserve company names, "
                            "tag numbers, drawing/document numbers, standards, units, formulas, "
                            "placeholders and style tags exactly. Do not omit or summarize. Return "
                            "JSON only as {\"translations\":[{\"id\":\"...\",\"text\":\"...\"}]}.")
                    },
                    {
                        "role": "user",
                        "content": json.dumps(repair_payload, ensure_ascii=False),
                    },
                ],
                request_json_mode=True,
                model=self.repair_model,
            )
            repair_content = self._remove_cot_content(
                response.choices[0].message.content.strip()
            )
            repaired_payload = json.loads(self._extract_json(repair_content))
            repaired_rows = repaired_payload.get("translations", [])
            repaired = {
                str(row.get("id")): str(row.get("text", "")).strip()
                for row in repaired_rows
                if isinstance(row, dict) and str(row.get("text", "")).strip()
            }
            result = dict(translations)
            result.update(repaired)
            logger.info(
                "RFQREPAIR items=%s seconds=%.3f model=%s",
                len(suspects),
                time.perf_counter() - started,
                self.repair_model,
            )
            return result
        except Exception as exc:
            logger.warning(
                "Residual Cyrillic repair failed; primary translation retained: %s",
                exc,
            )
            return translations

    def _worker_loop(self) -> None:
        while not self._closed:
            try:
                first = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            batch = [first]
            char_count = len(first.text)
            deadline = time.monotonic() + (self.flush_ms / 1000.0)
            while len(batch) < self.batch_size and char_count < self.max_chars:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                item_chars = len(item.text)
                if batch and char_count + item_chars > self.max_chars:
                    self._queue.put(item)
                    break
                batch.append(item)
                char_count += item_chars
            self._request_executor.submit(self._process_batch, batch)

    def _process_batch(self, batch: list[_BatchItem]) -> None:
        if not batch:
            return
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                translations = self._call_batch(batch)
                missing = [item.item_id for item in batch if item.item_id not in translations]
                if missing:
                    raise ValueError(
                        "OpenAI-compatible batch response missed ids: "
                        + ", ".join(missing[:5])
                    )
                for item in batch:
                    item.result = translations[item.item_id]
                    item.done.set()
                return
            except (
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.InternalServerError,
            ) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(8, 2 ** (attempt - 1)))
                    continue
                break
            except Exception as exc:
                last_error = exc
                break

        if len(batch) > 1:
            midpoint = max(1, len(batch) // 2)
            logger.warning(
                "Batched response failed; retrying two smaller groups (%s items): %s",
                len(batch),
                last_error,
            )
            self._process_batch(batch[:midpoint])
            self._process_batch(batch[midpoint:])
            return
        self._fail_batch(batch, last_error or RuntimeError("Batch request failed"))

    def _call_batch(self, batch: list[_BatchItem]) -> dict[str, str]:
        started = time.perf_counter()
        request_payload = {
            "items": [{"id": item.item_id, "text": item.text} for item in batch]
        }
        system_rules = self.custom_system_prompt.strip()
        if system_rules:
            system_rules += "\n\n"
        system_rules += (
            "Apply those translation rules independently to every input item. "
            "Preserve numbers, units, tag numbers, document numbers, standards, formulas, "
            "company names, placeholders such as {v1}, and style tags exactly. "
            "Do not merge, omit, summarize, or explain any item. Return JSON only in this "
            'shape: {"translations":[{"id":"...","text":"..."}]}.'
        )
        response = self._request(
            messages=[
                {"role": "system", "content": system_rules},
                {
                    "role": "user",
                    "content": json.dumps(request_payload, ensure_ascii=False),
                },
            ],
            request_json_mode=False,
        )
        self.batch_request_count.inc()
        logger.info(
            "RFQBATCH items=%s chars=%s seconds=%.3f model=%s",
            len(batch),
            sum(len(item.text) for item in batch),
            time.perf_counter() - started,
            self.model,
        )
        content = self._remove_cot_content(response.choices[0].message.content.strip())
        payload = json.loads(self._extract_json(content))
        translations = payload.get("translations", [])
        result: dict[str, str] = {}
        for row in translations:
            item_id = str(row.get("id", ""))
            translated = str(row.get("text", "")).strip()
            if item_id and translated:
                result[item_id] = translated
        return self._repair_translation_map(result)

    def _request(
        self,
        messages: list[dict],
        request_json_mode: bool,
        model: str | None = None,
    ):
        request_model = model or self.model
        options = self._provider_options(request_model)
        if request_json_mode:
            options["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(
            model=request_model,
            messages=messages,
            **options,
        )
        self._record_usage(response)
        return response

    def _provider_options(self, model: str | None = None) -> dict:
        if not self.disable_thinking:
            return {}
        normalized = (model or self.model).lower()
        if normalized.startswith("gemini-"):
            return {"extra_body": {"thinking_config": {"thinking_budget": 0}}}
        if normalized.startswith("glm-4.5"):
            return {
                "extra_body": {
                    "thinking": {"type": "disabled"},
                    "do_sample": False,
                }
            }
        return {}

    def _record_usage(self, response) -> None:
        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return
            for field, counter in (
                ("total_tokens", self.token_count),
                ("prompt_tokens", self.prompt_token_count),
                ("completion_tokens", self.completion_token_count),
            ):
                value = getattr(usage, field, None)
                if value is not None:
                    counter.inc(value)
            details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = getattr(details, "cached_tokens", None)
            if cached_tokens is not None:
                self.cache_hit_prompt_token_count.inc(cached_tokens)
        except Exception as exc:
            logger.error("Error reading provider token usage: %s", exc)

    @staticmethod
    def _extract_json(content: str) -> str:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("OpenAI-compatible batch response did not contain JSON")
        return cleaned[start : end + 1]

    @staticmethod
    def _fail_batch(batch: list[_BatchItem], exc: Exception) -> None:
        for item in batch:
            item.error = exc
            item.done.set()
