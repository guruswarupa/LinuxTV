#!/usr/bin/env python3
"""Script to update auth config with password_simple_hash"""
import hashlib
import yaml

config_path = "/home/guru/.config/linuxtv/config.yaml"

# Read existing config
with open(config_path, 'r') as f:
    config = yaml.safe_load(f)

# Get existing auth
auth = config.get('auth', {})
username = auth.get('username', '')
password_hash = auth.get('password_hash', '')
password_salt = auth.get('password_salt', '')

if username and password_hash and password_salt:
    print(f"Found existing credentials for user: {username}")
    print("To add password_simple_hash, you need to re-enter your password")
    
    password = input("Enter your password: ")
    
    # Generate simple hash
    password_simple_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    
    # Update config
    config['auth']['password_simple_hash'] = password_simple_hash
    
    # Save updated config
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    
    print("Config updated successfully with password_simple_hash")
else:
    print("No existing credentials found or incomplete auth config")