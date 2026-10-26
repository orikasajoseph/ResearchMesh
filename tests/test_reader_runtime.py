"""Reader 配置/预检边界；仅使用合成配置与依赖替身。"""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from reader_adapter import ReaderAdapterError
from reader_runtime import prepare_live_reader


class RuntimeTests(unittest.TestCase):
    values = {"DEEPSEEK_API_KEY": "synthetic-file-key", "DEEPSEEK_BASE_URL": "https://example.invalid",
              "DEEPSEEK_MODEL": "synthetic-model"}

    @staticmethod
    def make_embedding_cache(directory):
        root = Path(directory) / "sentence_transformers" / "models--sentence-transformers--multi-qa-MiniLM-L6-cos-v1"
        revision = "synthetic-revision"
        (root / "refs").mkdir(parents=True)
        (root / "refs" / "main").write_text(revision, encoding="utf-8")
        snapshot = root / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for name in ("config.json", "modules.json", "model.safetensors", "tokenizer.json"):
            (snapshot / name).touch()

    def test_missing_config_before_import_or_environment_mutation(self):
        with patch.dict(os.environ, {}, clear=True), patch("reader_runtime.dotenv_values", return_value={}), \
                patch("reader_runtime.load_reader") as load:
            with self.assertRaisesRegex(ReaderAdapterError, "MISSING_READER_CONFIGURATION"):
                prepare_live_reader("unused", env_file="synthetic.env")
            load.assert_not_called()
            self.assertNotIn("HF_HOME", os.environ)

    def test_missing_dependencies_before_wrapper(self):
        with patch.dict(os.environ, {}, clear=True), patch("reader_runtime.dotenv_values", return_value=self.values), \
                patch("reader_runtime.importlib.util.find_spec", return_value=None), patch("reader_runtime.load_reader") as load:
            with self.assertRaisesRegex(ReaderAdapterError, "READER_RUNTIME_DEPENDENCIES_MISSING"):
                prepare_live_reader("unused", env_file="synthetic.env")
            load.assert_not_called()

    def test_missing_cache_before_wrapper(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch("reader_runtime.dotenv_values", return_value=self.values), \
                patch("reader_runtime.importlib.util.find_spec", return_value=object()), patch("reader_runtime.load_reader") as load:
            with self.assertRaisesRegex(ReaderAdapterError, "READER_EMBEDDING_CACHE_MISSING"):
                prepare_live_reader("unused", env_file="synthetic.env", runtime_home=directory)
            load.assert_not_called()

    def test_project_config_and_process_override_used_not_wrapper_env(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "huggingface").mkdir()
            self.make_embedding_cache(directory)
            wrapper = SimpleNamespace(PaperReaderConfig=Mock(return_value=SimpleNamespace(provider_model="deepseek/synthetic")),
                PaperQAProvider=Mock(return_value="provider"), PaperReaderAgent=Mock(return_value="agent"), PaperReadTask="task-model")
            def load(root):
                self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
                self.assertEqual(os.environ["HF_HOME"], str(Path(directory) / "huggingface"))
                self.assertEqual(os.environ["HF_HUB_CACHE"], str(Path(directory) / "sentence_transformers"))
                self.assertEqual(os.environ["SENTENCE_TRANSFORMERS_HOME"], str(Path(directory) / "sentence_transformers"))
                return wrapper
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-process-key"}, clear=True), \
                    patch("reader_runtime.dotenv_values", return_value=self.values) as dotenv, \
                    patch("reader_runtime.importlib.util.find_spec", return_value=object()), \
                    patch("reader_runtime.load_reader", side_effect=load):
                self.assertEqual(prepare_live_reader("wrapper", env_file="main.env", runtime_home=directory), ("agent", "task-model"))
                dotenv.assert_called_once_with("main.env", encoding="utf-8-sig", interpolate=False)
                wrapper.PaperReaderConfig.assert_called_once_with(api_key="synthetic-process-key", api_base="https://example.invalid", model="synthetic-model")

    def test_local_pdf_fetcher_forwarded_to_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "huggingface").mkdir()
            self.make_embedding_cache(directory)
            config = SimpleNamespace(provider_model="deepseek/synthetic")
            wrapper = SimpleNamespace(PaperReaderConfig=Mock(return_value=config),
                PaperQAProvider=Mock(return_value="provider"), PaperReaderAgent=Mock(return_value="agent"),
                PaperReadTask="model")
            fetch = object()
            with patch.dict(os.environ, {}, clear=True), \
                    patch("reader_runtime.dotenv_values", return_value=self.values), \
                    patch("reader_runtime.importlib.util.find_spec", return_value=object()), \
                    patch("reader_runtime.load_reader", return_value=wrapper):
                prepare_live_reader("wrapper", env_file="main.env", runtime_home=directory, pdf_fetcher=fetch)
                wrapper.PaperQAProvider.assert_called_once_with(config, pdf_fetcher=fetch)


if __name__ == "__main__":
    unittest.main()
