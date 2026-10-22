"""Formal Reader adapter; importing does not create a provider or connect MQ."""
from aip.group_analysis_partner import serve_analysis
from aip.group_reader_handler import ReaderGroupHandler
from aip.reader_unified_executor import ReaderExecutor


async def serve(*, partner_aic, leader_aic, credential_dir, task_store,
                reader_python, reader_root, audit, env_file=None, runtime_home=None):
    executor = ReaderExecutor(reader_python=reader_python, reader_root=reader_root,
                              env_file=env_file, runtime_home=runtime_home)
    executor.validate_runtime_paths()
    await serve_analysis(partner_aic=partner_aic, leader_aic=leader_aic,
        credential_dir=credential_dir, task_store=task_store, executor=executor,
        handler_type=ReaderGroupHandler, audit=audit)
