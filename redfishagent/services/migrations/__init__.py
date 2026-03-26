"""Helper module for managing database migrations"""
import os
from pathlib import Path
from importlib import import_module, util
from typing import List, Tuple

def get_migration_files() -> List[Tuple[int, str]]:
    """Get all migration files sorted by version number"""
    migrations_dir = Path(__file__).parent  # The migrations are in the same directory as __init__.py
    migration_files = []
    
    for file in migrations_dir.glob('*.py'):
        if file.name.startswith('__'):
            continue
            
        try:
            version = int(file.stem.split('_')[0])
            migration_files.append((version, str(file)))
        except (ValueError, IndexError):
            continue
            
    return sorted(migration_files, key=lambda x: x[0])