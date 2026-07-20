from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from pdf_runtime.config import BABELDOC_VERSION
from pdf_runtime.config import PDF2ZH_NEXT_VERSION
from pdf_runtime.config import env_enabled
from pdf_runtime.config import env_int


def _install_batch_translator() -> None:
    from pdf_runtime import openai_batch_adapter

    sys.modules["pdf2zh_next.translator.translator_impl.openai"] = (
        openai_batch_adapter
    )


def _verify_runtime_versions() -> None:
    for distribution, expected in (
        ("pdf2zh-next", PDF2ZH_NEXT_VERSION),
        ("babeldoc", BABELDOC_VERSION),
    ):
        try:
            actual = version(distribution)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"PDF 翻译运行时缺少 {distribution}") from exc
        if actual != expected:
            raise RuntimeError(
                f"PDF 翻译运行时版本不兼容：{distribution}={actual}，要求 {expected}"
            )


def _configure_babeldoc_batches() -> None:
    from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import (
        ILTranslatorLLMOnly,
    )

    token_floor = env_int(
        "B_PDF_TRANSLATION_LLM_SHORT_TEXT_TOKEN_FLOOR", 8, maximum=64
    )
    current_counter = ILTranslatorLLMOnly.calc_token_count
    if getattr(current_counter, "_rfq_short_label_floor", None) != token_floor:
        original_counter = getattr(current_counter, "_rfq_original", current_counter)

        def rfq_calc_token_count(self, text: str) -> int:
            count = original_counter(self, text)
            if isinstance(text, str) and text.strip():
                return max(token_floor, count)
            return count

        rfq_calc_token_count._rfq_short_label_floor = token_floor
        rfq_calc_token_count._rfq_original = original_counter
        ILTranslatorLLMOnly.calc_token_count = rfq_calc_token_count

    token_limit = env_int(
        "B_PDF_TRANSLATION_BABELDOC_BATCH_TOKEN_LIMIT", 1600, maximum=8000
    )
    count_limit = env_int(
        "B_PDF_TRANSLATION_BABELDOC_BATCH_COUNT_LIMIT", 40, maximum=100
    )
    process_page = ILTranslatorLLMOnly.process_page
    desired = (token_limit, count_limit)
    if getattr(process_page, "_rfq_batch_limits", None) == desired:
        return
    original_code = getattr(process_page, "_rfq_original_code", process_page.__code__)
    token_marker_count = sum(type(value) is int and value == 200 for value in original_code.co_consts)
    count_marker_count = sum(type(value) is int and value == 5 for value in original_code.co_consts)
    if token_marker_count < 1 or count_marker_count < 1:
        raise RuntimeError(
            "BabelDOC 批量补丁目标常量不存在；请重新安装锁定版本后再试"
        )
    constants = tuple(
        token_limit
        if type(value) is int and value == 200
        else count_limit
        if type(value) is int and value == 5
        else value
        for value in original_code.co_consts
    )
    process_page.__code__ = original_code.replace(co_consts=constants)
    process_page._rfq_batch_limits = desired
    process_page._rfq_original_code = original_code


def _configure_doclayout() -> None:
    from babeldoc.docvision.doclayout import OnnxModel

    image_size = env_int(
        "B_PDF_TRANSLATION_DOCLAYOUT_IMAGE_SIZE", 800, maximum=1280
    )
    if image_size < 640 or image_size > 1280 or image_size % 32:
        raise ValueError(
            "B_PDF_TRANSLATION_DOCLAYOUT_IMAGE_SIZE 必须是 640-1280 之间的 32 倍数"
        )
    predict = OnnxModel.predict
    if getattr(predict, "_rfq_image_size", None) == image_size:
        return
    original_code = getattr(predict, "_rfq_original_code", predict.__code__)
    if not any(type(value) is int and value == 1024 for value in original_code.co_consts):
        raise RuntimeError(
            "BabelDOC DocLayout 补丁目标常量不存在；请重新安装锁定版本后再试"
        )
    constants = tuple(
        image_size if type(value) is int and value == 1024 else value
        for value in original_code.co_consts
    )
    predict.__code__ = original_code.replace(co_consts=constants)
    predict._rfq_image_size = image_size
    predict._rfq_original_code = original_code


def _configure_cyrillic_formula_handling() -> None:
    if not env_enabled(
        "B_PDF_TRANSLATION_TRANSLATE_CYRILLIC_FORMULA_TEXT", True
    ):
        return
    from babeldoc.format.pdf.document_il.midend import styles_and_formulas
    from babeldoc.format.pdf.document_il.utils import formular_helper

    current = formular_helper.is_formulas_start_char
    if getattr(current, "_rfq_cyrillic_translatable", False):
        styles_and_formulas.is_formulas_start_char = current
        return
    original = getattr(current, "_rfq_original", current)

    def rfq_is_formulas_start_char(char, font_mapper, translation_config):
        if char and re.match(r"[\u0400-\u04ff]", char):
            return False
        return original(char, font_mapper, translation_config)

    rfq_is_formulas_start_char._rfq_cyrillic_translatable = True
    rfq_is_formulas_start_char._rfq_original = original
    formular_helper.is_formulas_start_char = rfq_is_formulas_start_char
    styles_and_formulas.is_formulas_start_char = rfq_is_formulas_start_char


def _configure_same_text_policy() -> None:
    from pdf2zh_next import high_level

    current = high_level.create_babeldoc_config
    if getattr(current, "_rfq_same_text_policy", False):
        return
    original = current

    def rfq_create_babeldoc_config(settings, file):
        config = original(settings, file)
        if env_enabled("B_PDF_TRANSLATION_DISABLE_SAME_TEXT_FALLBACK", False):
            config.disable_same_text_fallback = True
        return config

    rfq_create_babeldoc_config._rfq_same_text_policy = True
    high_level.create_babeldoc_config = rfq_create_babeldoc_config


def _install_child_hooks() -> None:
    _install_batch_translator()
    _configure_babeldoc_batches()
    _configure_doclayout()
    _configure_cyrillic_formula_handling()
    _configure_same_text_policy()


def patched_translate_wrapper(*args, **kwargs):
    from pdf2zh_next import high_level

    original = getattr(
        high_level,
        "_rfq_original_translate_wrapper",
        high_level._translate_wrapper,
    )
    _install_child_hooks()
    return original(*args, **kwargs)


def install() -> None:
    from pdf2zh_next import high_level

    _verify_runtime_versions()
    _install_batch_translator()
    if high_level._translate_wrapper is patched_translate_wrapper:
        return
    high_level._rfq_original_translate_wrapper = high_level._translate_wrapper
    high_level._translate_wrapper = patched_translate_wrapper
