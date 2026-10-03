"""PyInstaller entry point for the frozen uukanshu binary.

The package uses relative imports, so the frozen app must import it as a
package instead of executing uukanshu/__init__.py as a script. See
uukanshu.spec and docs/DEVELOPMENT.md.
"""

from uukanshu import main

if __name__ == "__main__":
    main()
