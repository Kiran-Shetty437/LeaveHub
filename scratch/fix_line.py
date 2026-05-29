import re

with open(r'c:\D\Code_Predators\app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The broken line contains literal \r\n sequences
old = "    if session.get('role') != 'Teacher': return redirect(url_for('login'))\\r\\n    \\r\\n    # Only mentors can access class analytics\\r\\n    if not session.get('is_mentor'):\\r\\n        flash('Class Analytics is only available for mentors.', 'warning')\\r\\n        return redirect(url_for('dashboard'))"

new = """    if session.get('role') != 'Teacher': return redirect(url_for('login'))
    
    # Only mentors can access class analytics
    if not session.get('is_mentor'):
        flash('Class Analytics is only available for mentors.', 'warning')
        return redirect(url_for('dashboard'))"""

if old in content:
    content = content.replace(old, new)
    with open(r'c:\D\Code_Predators\app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed successfully!')
else:
    print('Pattern not found - checking raw bytes...')
    # Try to find it
    idx = content.find("monthly_reports():")
    if idx >= 0:
        print(repr(content[idx:idx+400]))
