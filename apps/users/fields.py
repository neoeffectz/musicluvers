from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models

def get_fernet():
    return Fernet(settings.FERNET_KEY.encode())

class EncryptedTextField(models.TextField):
    """Transparently encrypts/decrypts on save/load."""

    def get_prep_value(self, value):
        if value is None:
            return value
        return get_fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return get_fernet().decrypt(value.encode()).decode()