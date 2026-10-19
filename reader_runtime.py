"""显式 Reader 运行时；从主项目配置读取 Key，不修改 conda 或安装依赖。"""
import importlib.util
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

from reader_adapter import ReaderAdapterError, load_reader
from reader_batch_adapter import call_reader_batch


def prepare_live_reader(root, *, env_file, runtime_home=None, pdf_fetcher=None):
    values = dict(dotenv_values(env_file, encoding="utf-8-sig", interpolate=False))
    for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        if key in os.environ:
            values[key] = os.environ[key]
        if not isinstance(values.get(key), str) or not values[key].strip():
            raise ReaderAdapterError("MISSING_READER_CONFIGURATION")
    if any(importlib.util.find_spec(name) is None for name in ("paperqa", "sentence_transformers", "torch")):
        raise ReaderAdapterError("READER_RUNTIME_DEPENDENCIES_MISSING")
    cache = Path(runtime_home if runtime_home is not None else Path(sys.prefix) / "runtime-cache")
    if not (cache / "huggingface").is_dir():
        raise ReaderAdapterError("READER_EMBEDDING_CACHE_MISSING")
    # 在导入依赖前关闭自动下载及遥测。此入口要求已准备好的离线 embedding。
    os.environ.update(HF_HOME=str(cache / "huggingface"), PQA_HOME=str(cache / "paperqa"),
                      TIKTOKEN_CACHE_DIR=str(cache / "tiktoken"), HF_HUB_OFFLINE="1",
                      TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      HF_HUB_DISABLE_IMPLICIT_TOKEN="1", DO_NOT_TRACK="1",
                      LITELLM_LOCAL_MODEL_COST_MAP="True")
    try:
        wrapper = load_reader(root)
        config = wrapper.PaperReaderConfig(api_key=values["DEEPSEEK_API_KEY"],
                                          api_base=values["DEEPSEEK_BASE_URL"],
                                          model=values["DEEPSEEK_MODEL"])
        config.provider_model
        # 单篇本地 PDF 验收可显式注入文件读取器；默认网络行为不变。
        provider = (wrapper.PaperQAProvider(config) if pdf_fetcher is None
                    else wrapper.PaperQAProvider(config, pdf_fetcher=pdf_fetcher))
        return wrapper.PaperReaderAgent(provider), wrapper.PaperReadTask
    except ReaderAdapterError:
        raise
    except Exception:
        raise ReaderAdapterError("READER_RUNTIME_PREPARATION_FAILED") from None


async def execute_reader_task(payload, *, prepared, timeout_seconds=600,
                              saved_outcomes=None, on_paper=None, on_start=None):
    agent, task_model = prepared
    return await call_reader_batch(payload, agent, task_model, timeout_seconds=timeout_seconds,
                                   saved_outcomes=saved_outcomes, on_paper=on_paper, on_start=on_start)
