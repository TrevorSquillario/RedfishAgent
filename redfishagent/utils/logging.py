import os
import logging

def setup_logger(name: str = None) -> logging.Logger:
    """
    Configure and return a logger with consistent formatting and level.
    
    Args:
        name: The name of the logger. If None, returns the root logger.
    
    Returns:
        A configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    
    # Only add handlers if they haven't been added yet (avoid double logs)
    if not logger.handlers and not logger.hasHandlers():
        formatter = logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # Set level based on environment variable
    logger.setLevel(os.getenv('LOG_LEVEL', "DEBUG"))
    
    return logger

# Configure root logger
root_logger = setup_logger()