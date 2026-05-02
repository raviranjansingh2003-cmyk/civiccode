CIVIC CODE — LOCAL SETUP GUIDE

1. Install Python
   Recommended version: Python 3.11 or Python 3.12

2. Open terminal inside the project folder

3. Create virtual environment
   Windows:
      python -m venv venv
      venv\Scripts\activate

   Mac/Linux:
      python3 -m venv venv
      source venv/bin/activate

4. Install dependencies
      pip install -r requirements.txt

5. Create .env file
   Copy:
      .env.example
   Rename it to:
      .env

6. Run the app
      python run.py

7. Open browser
      http://localhost:5000

-----------------------------------
UPDATED DESIGN
-----------------------------------
• New stylish black & pink app icons
• Better compatibility for local Python execution
• Ready for Flask local development

