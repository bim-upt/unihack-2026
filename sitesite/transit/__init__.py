# Load .env once, before any settings are read.
from .env import load_env_file

load_env_file()