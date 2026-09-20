import sys
for filepath in sys.argv[1:]:
    print(f"--- {filepath} ---")
    with open(filepath, 'r') as f:
        for line in f:
            if line.startswith('#'):
                print(line.strip())
