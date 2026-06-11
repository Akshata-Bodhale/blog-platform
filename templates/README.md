# 📝 BlogSphere

A full-stack blog platform built with **Flask** and **MySQL** where users can write, share, and explore blogs across multiple categories.

---

## 🚀 Features

- 🔐 User Registration & Login with password hashing
- ✍️ Create, Edit, and Delete blogs with image uploads
- 📂 Categories — Travel, Food, Technology, Education, Lifestyle
- 💬 Comment system on blog posts
- 👤 Author profiles with profile picture
- 🏅 Trusted Author badge (earned after 20 approved blogs)
- 🛡️ Admin Dashboard to manage users, blogs, comments, and reports
- 🚩 Report system for inappropriate content

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, Flask |
| Database | MySQL |
| Frontend | HTML, CSS, JavaScript |
| Auth | Werkzeug password hashing |
| File Uploads | Flask file handling |

---

## ⚙️ Setup Instructions

### 1. Clone the repository
```bash
git clone https://github.com/Akshata-Bodhale/blog-platform.git
cd blog-platform
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Create your `.env` file
Copy the example file and fill in your details:
```bash
cp .env.example .env
```

Edit `.env` with your actual values:
```
SECRET_KEY=your-secret-key-here
MYSQL_HOST=localhost
MYSQL_USER=your_db_username
MYSQL_PASSWORD=your_db_password
MYSQL_DB=blog_db
```

### 4. Set up the MySQL database
- Create a database named `blog_db` in MySQL
- Import the schema  or let Flask create tables on first run

### 5. Run the application
```bash
python app.py
```

Visit `http://localhost:5000` in your browser.

---

## 📁 Project Structure

```
blog-platform/
├── app.py              # Main Flask application
├── templates/          # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── blog_detail.html
│   ├── create_blog.html
│   └── admin/
│       ├── dashboard.html
│       ├── users.html
│       ├── blogs.html
│       └── reports.html
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore rules
└── requirements.txt    # Python dependencies
```

---

## 🔒 Security Notes

- Passwords are hashed using **Werkzeug** — never stored as plain text
- All secrets are stored in `.env` file — never committed to GitHub
- File uploads are validated for allowed extensions only
- Admin routes are protected with login and role checks

---

## 👩‍💻 Author

**Akshata Bodhale**  
GitHub: [@Akshata-Bodhale](https://github.com/Akshata-Bodhale)

---

## 📄 License

This project is for educational purposes.
