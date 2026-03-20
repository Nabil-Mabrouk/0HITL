import os

# --- CONFIGURATION ---
# Directories to completely ignore
IGNORE_DIRS = {
    'node_modules', '.next', '.venv', '.git', '.vincent', '.turbo'
    'dist', 'build', '__pycache__', '.vscode', 'Book', 'docs', 'ressources', 'venv'
}

# Specific files to ignore (like heavy lock files)
IGNORE_FILES = {
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', '.DS_Store'
}

# File extensions to include
INCLUDE_EXTENSIONS = {
    '.ts', '.tsx', '.js', '.jsx', '.json', '.prisma', 
    '.py', '.css', '.html', '.env', '.yml', '.yaml', '.mjs', '.md'
}

OUTPUT_FILE = "project_summary.txt"

def generate_tree(startpath):
    tree_str = "--- FILE SYSTEM STRUCTURE ---\n"
    for root, dirs, files in os.walk(startpath):
        # Remove ignored directories from search
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * level
        tree_str += f"{indent}{os.path.basename(root)}/\n"
        sub_indent = ' ' * 4 * (level + 1)
        
        for f in sorted(files):
            # Only show files in tree if they aren't ignored
            if f not in IGNORE_FILES and (any(f.endswith(ext) for ext in INCLUDE_EXTENSIONS) or f.startswith('.')):
                tree_str += f"{sub_indent}{f}\n"
    return tree_str

def get_file_contents(startpath):
    content_str = "\n--- FILE CONTENTS ---\n"
    for root, dirs, files in os.walk(startpath):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        for file in sorted(files):
            if file in IGNORE_FILES:
                continue
                
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, startpath)
            
            # Only read the file if it has a relevant extension
            if any(file.endswith(ext) for ext in INCLUDE_EXTENSIONS):
                content_str += f"\n{'='*80}\n"
                content_str += f"FILE: {relative_path}\n"
                content_str += f"{'='*80}\n"
                
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content_str += f.read() + "\n"
                except Exception as e:
                    content_str += f"[Error reading file: {e}]\n"
    return content_str

def main():
    root_dir = os.getcwd()
    print(f"Scanning: {root_dir}")
    
    tree = generate_tree(root_dir)
    contents = get_file_contents(root_dir)
    
    with open(OUTPUT_FILE, "w", encoding='utf-8') as output:
        output.write(tree)
        output.write(contents)
        
    print(f"Success! Project summary saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()