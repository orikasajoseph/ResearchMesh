"""One isolated, non-retrying Reader worker per durably reserved Group task."""
from pathlib import Path
from uuid import uuid4

from aip_reader_partner import create_reader_business_executor
from aip.literature_audit import current_task_identity
from business_execution import execute_business_task
from reader_adapter import to_reader_request

ROOT = Path(__file__).resolve().parents[1]


class ReaderExecutor:
    def __init__(self, *, reader_python, reader_root, env_file=None, runtime_home=None):
        self.python = Path(reader_python).resolve()
        self.reader_root = Path(reader_root).resolve()
        self.env_file = Path(env_file).resolve() if env_file else None
        self.runtime_home = Path(runtime_home).resolve() if runtime_home else None

    def validate_runtime_paths(self):
        if not self.python.is_file():
            raise ValueError("READER_PYTHON_NOT_FOUND")
        if not (self.reader_root / "researchmesh_paper_reader" / "__init__.py").is_file():
            raise ValueError("READER_WRAPPER_NOT_FOUND")
        if self.env_file and not self.env_file.is_file():
            raise ValueError("READER_ENV_FILE_NOT_FOUND")
        if self.runtime_home and not (self.runtime_home / "huggingface").is_dir():
            raise ValueError("READER_EMBEDDING_CACHE_NOT_FOUND")

    async def __call__(self, task):
        payload = task.model_dump(mode="json") if hasattr(task, "model_dump") else task
        checked = to_reader_request(payload)
        if not checked["input"]["papers"][0].get("pdfUrl"):
            raise ValueError("VERIFIED_PDF_URL_REQUIRED")
        self.validate_runtime_paths()
        # The backend's path boundary permits only project-owned unique outputs.
        worker_run_id = uuid4().hex
        output = ROOT / "outputs" / ("reader-group-" + worker_run_id)
        output.mkdir(parents=True, exist_ok=False)
        identity = current_task_identity()
        audit_kwargs = {}
        if identity is not None:
            if any(not identity.get(key) for key in ("groupId", "sessionId", "taskId")):
                raise ValueError("READER_TASK_IDENTITY_INVALID")
            audit_kwargs = {"audit_identity": identity, "worker_run_id": worker_run_id}
        backend = create_reader_business_executor(record_dir=output, reader_python=self.python,
            reader_root=self.reader_root, dynamic=True, env_file=self.env_file,
            runtime_home=self.runtime_home, resume_journal=None, **audit_kwargs)
        return await execute_business_task(checked, skill="paper.analyze", backend=backend,
                                           allow_execution=True, timeout_seconds=660)
