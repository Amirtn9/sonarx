from cryptography.fernet import Fernet
class SecretBox:
    def __init__(self,key):
        if not key: raise RuntimeError("FERNET_KEY is required to store panel passwords securely.")
        self.f=Fernet(key.encode() if isinstance(key,str) else key)
    def encrypt(self,value): return self.f.encrypt(value.encode()).decode()
    def decrypt(self,value): return self.f.decrypt(value.encode()).decode()
