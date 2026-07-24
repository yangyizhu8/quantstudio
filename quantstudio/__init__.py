"""QuantStudio"""
__version__ = "0.3.2+mvp"

# 自动载入 config/secrets.env 凭证到进程环境变量（幂等，不覆盖已有变量）
from ._secrets import load_secrets_env

load_secrets_env()
