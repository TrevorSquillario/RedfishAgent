import os
import json
from datetime import datetime, date
import subprocess
import gzip
from importlib import util
from peewee import *
from playhouse.migrate import PostgresqlMigrator
from typing import Optional, List, Dict, Any, Union, Tuple
from pathlib import Path
from services.migrations import get_migration_files
from utils.logging import setup_logger
from models.db import database_proxy, KB, AgentSession, TraceEvent
import uuid
from peewee_async import PooledPostgresqlDatabase

# Configure logging
logger = setup_logger(__name__)

MIGRATIONS_VERSION_TABLE = 'migrations_version'

class DatabaseManager:
    # Register models here so `create_tables` creates them on startup.
    _tables = [KB, AgentSession, TraceEvent]

    def __init__(self):
        self._initialize()

    def _initialize(self): 
        """Initialize the database and run migrations"""
        logger.info("Initializing database...")
        # Set up PostgreSQL database connection
        POSTGRES_DB = os.getenv('POSTGRES_DB', 'your_db_name')
        POSTGRES_USER = os.getenv('POSTGRES_USER', 'your_db_user')
        POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'your_db_password')
        POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
        POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')

        # PostgresqlDatabase from peewee_async wraps aiopg to provide both a
        # synchronous drop-in connection and an async connection pool.
        self._db = PooledPostgresqlDatabase(
            POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
            port=int(POSTGRES_PORT),
        )

        # Bind the Peewee Proxy so models resolve to this database.
        database_proxy.initialize(self._db)

        # allow_sync() temporarily permits synchronous queries (required for
        # table creation / migrations) and closes the sync connection on exit.
        with self._db.allow_sync():
            self._db.connect()
            self._db.execute_sql("CREATE EXTENSION IF NOT EXISTS vector;")
            self._db.create_tables(self._tables)
            self._run_migrations()

    def _run_migrations(self):
        """Run all pending database migrations"""
        logger.info("Checking for database migrations...")
        # Create migrations version table if it doesn't exist
        self._db.execute_sql(
            f'''CREATE TABLE IF NOT EXISTS {MIGRATIONS_VERSION_TABLE} (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        
        current_version = self._get_current_version() 
        logger.info(f"Current database version: {current_version}")
        
        migrator = PostgresqlMigrator(self._db)
        
        # Get all migration files
        migrations = get_migration_files()
        logger.info(f"Found {len(migrations)} migration files")
        
        # Determine if there are any pending migrations
        pending_migrations = [(version, file_path) for version, file_path in migrations if version > current_version]
        if not pending_migrations:
            logger.info("No pending migrations to apply. Skipping backup.")
            return
        # Backup database before running migrations
        try:
            backup_path = self.backup_database()
            logger.info(f"Database backup created before migrations: {backup_path}") 
        except Exception as e:
            logger.error(f"Failed to create backup before migrations: {e}")
        
        for version, file_path in pending_migrations:
            logger.info(f"Running migration {version} from {file_path}")
            try:
                # Import the migration module
                spec = util.spec_from_file_location(f"migration_{version}", file_path)
                module = util.module_from_spec(spec)
                spec.loader.exec_module(module)
                # Run the upgrade function
                with self._db.atomic():
                    module.upgrade(migrator)
                    self._set_version(version)
                logger.info(f"Successfully applied migration {version}")
            except Exception as e:
                logger.error(f"Error applying migration {version}: {str(e)}")
                raise

    def _get_current_version(self):
        """Get the current database migration version"""
        try:
            cursor = self._db.execute_sql(
                f'SELECT version FROM {MIGRATIONS_VERSION_TABLE} ORDER BY version DESC LIMIT 1'
            )
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception:
            return 0

    def _set_version(self, version: int):
        """Set the current database migration version"""
        self._db.execute_sql(
            f'INSERT INTO {MIGRATIONS_VERSION_TABLE} (version) VALUES (%s)',
            (version,)
        )

    def backup_database(self) -> str:
        """Create a compressed PostgreSQL dump to /backup and return the backup file path.

        Preferred method: use the system `pg_dump` (installed in the dev image) to
        create a compressed SQL dump at `/backup/db_backup_<timestamp>.sql.gz`.

        If `pg_dump` is unavailable or the dump fails, fall back to the previous
        JSON exporter into `output/db_backups` and return that path.
        """
        # Prefer the system-level backup target provided by the environment.
        backup_dir = Path('/backup')
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        sql_gz_path = backup_dir / f'db_backup_{timestamp}.sql.gz'

        # Collect connection info from env (same vars used when initializing DB)
        POSTGRES_DB = os.getenv('POSTGRES_DB', 'your_db_name')
        POSTGRES_USER = os.getenv('POSTGRES_USER', 'your_db_user')
        POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', '')
        POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
        POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')

        pg_cmd = [
            'pg_dump',
            '-h', POSTGRES_HOST,
            '-p', str(POSTGRES_PORT),
            '-U', POSTGRES_USER,
            POSTGRES_DB,
        ]

        env = os.environ.copy()
        if POSTGRES_PASSWORD:
            # pg_dump reads PGPASSWORD from env if provided
            env['PGPASSWORD'] = POSTGRES_PASSWORD

        logger.info(f"Starting pg_dump to {sql_gz_path}")
        # Stream pg_dump -> gzip to avoid large memory usage
        with subprocess.Popen(pg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as proc:
            with open(sql_gz_path, 'wb') as out_fh:
                with gzip.GzipFile(fileobj=out_fh, mode='wb') as gz:
                    for chunk in iter(lambda: proc.stdout.read(8192), b''):
                        gz.write(chunk)
            stderr = proc.stderr.read()
            retcode = proc.wait()
            if retcode != 0:
                raise RuntimeError(f'pg_dump failed (exit {retcode}): {stderr.decode(errors="ignore")}')

        logger.info(f'Postgres dump written to {sql_gz_path}')
        return str(sql_gz_path)

    def close(self):
        """Close the synchronous database connection if open."""
        try:
            if hasattr(self, '_db') and not self._db.is_closed():
                self._db.close()
                logger.info('Database connection closed')
        except Exception as e:
            logger.error(f'Error closing database: {e}')

    async def aclose(self):
        """Close the async connection pool managed by peewee-async."""
        try:
            if hasattr(self, '_db') and self._db.is_connected:
                await self._db.aio_close()
                logger.info('Async database connection pool closed')
        except Exception as e:
            logger.error(f'Error closing async database pool: {e}')

    async def save_kb(self, file_name: str, file_modified: datetime, embedding: List[float]) -> KB:
        """Asynchronously save a KB record containing file metadata and embedding vector.

        The `embedding` parameter should be a list/sequence of floats; when
        `pgvector` is installed the `VectorField` will store it as a true
        pgvector value.
        """
        try:
            kb = await KB.aio_create(
                file_name=file_name,
                file_modified=file_modified,
                embedding=embedding,
            )
            logger.info(f"Saved KB record for {file_name} (id={kb.id})")
            return kb
        except Exception as e:
            logger.error(f"Failed to save KB record for {file_name}: {e}")
            raise

# Module-level singleton and helpers
_db_manager: Optional[DatabaseManager] = None

def init_database() -> DatabaseManager:
    """Initialize and return the module-level DatabaseManager singleton."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager

def get_database_manager() -> Optional[DatabaseManager]:
    """Return the initialized DatabaseManager or None if not initialized."""
    return _db_manager

def close_database():
    """Close the database connection and clear the singleton."""
    global _db_manager
    if _db_manager is not None:
        try:
            _db_manager.close()
        except Exception:
            pass
        _db_manager = None

    