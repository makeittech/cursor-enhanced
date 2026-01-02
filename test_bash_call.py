#!/usr/bin/env python3
"""Test script to verify direct bash call to cursor-agent"""
import subprocess
import os

def test_direct_bash_call():
    """Test the direct bash call as used in main.py line 219"""
    cursor_agent_path = os.path.expanduser("~/.local/bin/cursor-agent")
    
    print(f"Testing direct bash call to: {cursor_agent_path}")
    print(f"Path exists: {os.path.exists(cursor_agent_path)}")
    print(f"Path is executable: {os.access(cursor_agent_path, os.X_OK)}")
    print()
    
    # Test the exact call from main.py line 219
    test_args = ["-p", "Test direct bash call"]
    cmd = ["bash", cursor_agent_path] + test_args
    
    print(f"Command: {' '.join(cmd)}")
    print("Executing...")
    print("-" * 50)
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
        
        print(f"Return code: {result.returncode}")
        print(f"\nSTDOUT:\n{result.stdout}")
        if result.stderr:
            print(f"\nSTDERR:\n{result.stderr}")
            
    except subprocess.TimeoutExpired:
        print("Command timed out after 30 seconds")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_direct_bash_call()
