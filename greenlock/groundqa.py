#!/usr/bin/env python3
"""groundqa — обратная совместимость.

Все функции реэкспортируются из core/. Этот файл — тонкий шим,
чтобы `import groundqa as g` и `python3 groundqa.py "вопрос"` работали
как раньше.
"""
from greenlock.utils import (
    terms_of, _norm, _zero_usage, query_key_tokens,
    TRANSIENT_CODES, urlopen_retry,
)
from greenlock.citations import (
    CITE_RE, verify_citations, REFUSAL_MARK,
    is_refusal, is_low_content, escalation_reason,
)
from greenlock.index import (
    CODE_EXT, INDEX_EXT, INDEX_NAMES,
    SECRET_EXT, SECRET_HINTS, SKIP_DIRS,
    MAX_FILE_BYTES, MAX_LINE_LEN, WIN, OVERLAP,
    parse_yaml_keys, parse_md_sections, build_index,
)
from greenlock.search import (
    ollama_post, embed, chunk_key, default_cache_path,
    load_cache, save_cache, embed_chunks_cached,
    cosine, ROOT_AUTHORITATIVE, NOISE_DIR_HINTS, NOISE_FILES,
    path_score, retrieve, render_context, SIM_FLOOR,
)
from greenlock.qa import (
    SYSTEM_PROMPT,
    ask_ollama, gemini_chat, generate,
    answer_with, print_answer, main,
)
from greenlock.structural import (
    SYMBOL_TRIGGERS, STRUCT_SCALAR_FLOOR, STRUCT_TIE_DELTA,
    structural_answer,
)
from greenlock.code_writer import write_code

if __name__ == "__main__":
    main()
