import os

os.environ["JWT_SECRET_KEY"] = "eval-secret-key-not-for-production-1234567890"
os.environ["APP_ENV"] = "test"
os.environ["DEBUG"] = "False"
