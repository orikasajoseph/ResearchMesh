"""Formal static Repository adapter; no network on import."""
from aip.group_analysis_partner import serve_analysis
from aip.group_literature_partner import tls_context
from aip.group_repository_handler import RepositoryGroupHandler
from aip.repository_unified_executor import RepositoryStaticExecutor


async def serve(*, partner_aic, leader_aic, credential_dir, task_store,
                repository_python, wrapper_root, audit):
    executor = RepositoryStaticExecutor(repository_python=repository_python, wrapper_root=wrapper_root)
    executor.validate_runtime_paths()
    await serve_analysis(partner_aic=partner_aic, leader_aic=leader_aic,
        credential_dir=credential_dir, task_store=task_store, executor=executor,
        handler_type=RepositoryGroupHandler, audit=audit)
