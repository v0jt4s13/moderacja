import os
import ast
import sys

sys.stdout.reconfigure(encoding='utf-8')
used_modules_file = "requirements_generated.txt"
unused_modules_file = "unused_packages.txt"
installed_modules_file = "requirements.txt"

PROJECT_DIR = os.path.abspath(".")
builtin_modules = set(__import__('sys').builtin_module_names)
collected_imports = set()

def find_imports_in_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            node = ast.parse(f.read(), filename=filepath)
        except SyntaxError:
            return set()
    imports = set()
    for stmt in ast.walk(node):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(stmt, ast.ImportFrom):
            if stmt.module:
                imports.add(stmt.module.split(".")[0])
    return imports
# print(PROJECT_DIR)
# exit()
for root, _, files in os.walk(PROJECT_DIR):
    for file in files:
        if file.endswith(".py"):
            full_path = os.path.join(root, file)
            collected_imports.update(find_imports_in_file(full_path))

external_modules = sorted([
    mod for mod in collected_imports
    if mod not in builtin_modules and not mod.startswith("_")
])

with open(used_modules_file, "w", encoding="utf-8") as f:
    for mod in external_modules:
        f.write(mod + "\n")

print("📦 Wykryto używane zewnętrzne pakiety:")
print("\n".join(external_modules))
print(f"\n✅ Zapisano do {used_modules_file}")




with open(used_modules_file, "r", encoding="utf-8") as f:
    used = set([line.strip().lower() for line in f if line.strip()])

with open(installed_modules_file, "r", encoding="utf-8") as f:
    declared = set()
    for line in f:
        if "==" in line:
            declared.add(line.split("==")[0].strip().lower())

unused = sorted(declared - used)

print(f"🧹 Nieużywane pakiety z requirements.txt zapisane do {unused_modules_file}\n:")
for pkg in unused:
    print(f" - {pkg}")

# opcjonalnie zapisz do pliku
with open(unused_modules_file, "w", encoding="utf-8") as f:
    for pkg in unused:
        f.write(pkg + "\n")


