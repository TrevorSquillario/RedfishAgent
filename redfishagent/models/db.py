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
from peewee_async import AioModel

database_proxy = Proxy()

class BaseModel(AioModel):
    created_at = DateTimeTZField(default=lambda: datetime.now(timezone.utc))
    updated_at = DateTimeTZField(default=lambda: datetime.now(timezone.utc))

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now(timezone.utc)
        return super(BaseModel, self).save(*args, **kwargs)

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

class AgentSession(BaseModel):
    session_id = UUIDField(unique=True) # Used to generate the Trace Link for Jira
    service_tag = CharField(null=True)
    created_at = DateTimeField(default=datetime.now)

class TraceEvent(BaseModel):
    session = ForeignKeyField(AgentSession, backref='events')
    event_type = CharField() # 'prompt', 'thought', 'tool_call', 'tool_output', 'final_answer'
    content = TextField()
    created_at = DateTimeField(default=datetime.now)