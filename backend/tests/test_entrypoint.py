import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.builder import GraphBuilder

def test_entrypoint_scoring():
    # Create a temp directory with dummy files
    import tempfile
    import shutil
    
    tmp_dir = tempfile.mkdtemp()
    try:
        # 1. Convention (Python main) -> Score 100 + Filename 50 = 150
        (Path(tmp_dir) / "main.py").write_text('def start(): pass\nif __name__ == "__main__":\n    start()', encoding="utf-8")
        
        # 2. Filename Only (index.js) -> Score 50
        (Path(tmp_dir) / "index.js").write_text('function log() { console.log("hi"); }', encoding="utf-8")
        
        # 3. Name Only (run function in utils.py) -> Score 10
        (Path(tmp_dir) / "utils.py").write_text('def run():\n    pass', encoding="utf-8")
        
        builder = GraphBuilder(tmp_dir)
        builder.build_graph()
        payload = builder.to_json()
        
        nodes = payload["nodes"]
        # Filter only entry nodes (those with entry_score > 0)
        entry_nodes = [n for n in nodes if n["data"].get("entry_score", 0) > 0]
        
        # Sort by score for verification
        entry_nodes.sort(key=lambda n: n["data"]["entry_score"], reverse=True)
        
        print(f"Found {len(entry_nodes)} entry nodes.")
        for n in entry_nodes:
            print(f"Node: {n['id']}, Score: {n['data']['entry_score']}")
            
        # Assertions
        assert entry_nodes[0]["data"]["entry_score"] == 150  # main.py with convention
        assert "main.py" in entry_nodes[0]["id"]
        
        assert entry_nodes[1]["data"]["entry_score"] == 50   # index.js (filename only, no 'main' function/convention inside)
        assert "index.js" in entry_nodes[1]["id"]
        
        assert entry_nodes[2]["data"]["entry_score"] == 10   # utils.py::run (name only)
        assert "utils.py" in entry_nodes[2]["id"]
        
        print("✓ Entrypoint scoring verification passed!")
        
    finally:
        shutil.rmtree(tmp_dir)

if __name__ == "__main__":
    test_entrypoint_scoring()
