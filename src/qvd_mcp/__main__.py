"""Allow ``python -m qvd_mcp`` as an alias for the ``qvd-mcp`` script."""
from qvd_mcp.cli import app

if __name__ == "__main__":
    app()
