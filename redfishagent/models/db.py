import os
from datetime import datetime, date, timezone
from importlib import util
from peewee import *
from playhouse.postgres_ext import JSONField
from playhouse.postgres_ext import DateTimeTZField
from playhouse.migrate import PostgresqlMigrator
from typing import Optional
from pathlib import Path
from peewee import Proxy, CharField, DateTimeField
from typing import Optional
from pgvector.peewee import VectorField

database_proxy = Proxy()

class BaseModel(Model):
    created_at = DateTimeTZField(default=lambda: datetime.now(timezone.utc))
    updated_at = DateTimeTZField(default=lambda: datetime.now(timezone.utc))

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now(timezone.utc)
        return super(BaseModel, self).save(*arsgs, **kwargs)

    class Meta:
        database = database_proxy 

class KB(BaseModel):
    """Knowledge Base record storing file metadata and its vector embedding.

    Fields:
    - file_name: name or path of the source file
    - file_modified: timestamp when the file was last modified
    - embedding: vector embedding 
    """
    file_name = CharField(null=False)
    file_modified = DateTimeField(null=False)
    embedding = VectorField(null=False)