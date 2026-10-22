"""Prepare the isolated minimal static runtime, never target-repository dependencies."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
PIN = 'f0975ea19b17568ceb55cd5422206e1a1ae13c0b'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='允许创建隔离环境并下载静态依赖')
    parser.add_argument('--enhanced', action='store_true', help='同时安装共享 LLM 客户端依赖；安装本身不调用模型')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('请使用现有的 Python 3.12 .venv 运行此安装脚本。')
    if not args.execute:
        print('将创建 .venv-repository，安装固定静态依赖及 CodeWiki，预热分词缓存。不会调用模型。')
        if args.enhanced: print('额外安装共享 LLM 客户端，供显式 enhanced 模式使用。')
        print('加 --execute 才开始安装；不会改 Leader/Reader 环境。'); return
    environment = ROOT / '.venv-repository'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'agents/repository/requirements-static.txt')], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps',
                    'codewiki @ git+https://github.com/FSoft-AI4Code/CodeWiki.git@' + PIN], check=True)
    if args.enhanced:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r',
                        str(ROOT / 'agents/repository/requirements-enhanced.txt')], check=True)
    env = dict(os.environ, TIKTOKEN_CACHE_DIR=str(environment / 'runtime-cache/tiktoken'), PYTHON_DOTENV_DISABLED='1')
    subprocess.run([str(python), '-c', "import tiktoken; print('Tokenizer cache ready:', tiktoken.get_encoding('cl100k_base').n_vocab)"], env=env, check=True)
    print('安装完成；请运行 run_aip_repository.py 做无网络预检。')


if __name__ == '__main__':
    main()
