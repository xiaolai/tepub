# Installing TEPUB – Step by Step

This guide will help you install TEPUB on your computer, whether you're using Mac, Linux, or Windows.

**Don't worry if you're not technical!** We'll walk you through each step.

---

## What You'll Need

Before installing TEPUB, make sure you have:

- **A computer** with at least 2 GB of free memory (4 GB is better)
- **About 500 MB of free disk space** for the software
- **An internet connection** to download files
- **20-30 minutes** of your time

### Which Operating Systems Work?

- ✅ **Mac** – macOS 10.14 or newer
- ✅ **Linux** – Ubuntu 18.04+, Debian 10+, or similar
- ✅ **Windows** – Windows 10 or newer

---

## Quick Install (Easiest Way)

**For Mac and Linux users**, we have an automatic installer that does everything for you.

### Step 1: Open Terminal

- **Mac**: Press `Cmd + Space`, type "Terminal", and press Enter
- **Linux**: Press `Ctrl + Alt + T`

### Step 2: Copy and Paste These Commands

Copy this text exactly, paste it into Terminal, and press Enter after each line:

```bash
git clone https://github.com/xiaolai/tepub.git
cd tepub
./install.sh
```

### Step 3: Wait for Installation

The installer will:
- Install Python (the programming language TEPUB needs)
- Install helper programs for working with books
- Install ffmpeg (for making audiobooks)
- Set everything up properly

This takes about 5-10 minutes. You'll see text scrolling by – that's normal!

### Step 4: Activate TEPUB

After installation finishes, type this command:

```bash
source .venv/bin/activate
```

You're done! Skip to the [Test Your Installation](#test-your-installation) section below.

---

## Manual Installation

If the automatic installer doesn't work, or if you're on Windows, follow these instructions for your operating system.

### Installing on Mac

#### What You'll Do:
1. Install Homebrew (a tool installer)
2. Install helper programs
3. Install Python
4. Install TEPUB

#### Step-by-Step:

**1. Install Homebrew** (skip if you already have it)

Open Terminal and paste this command:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Press Enter when it asks for your password. This takes a few minutes.

**2. Install Helper Programs**

```bash
brew install libxml2 libxslt ffmpeg
```

This installs:
- `libxml2` and `libxslt` – for reading book files
- `ffmpeg` – for creating audiobooks

**3. Install Python 3.11**

```bash
brew install pyenv
```

Then add pyenv to your shell (copy all these lines together):

```bash
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc
source ~/.zshrc
```

Now install Python:

```bash
pyenv install 3.11.13
```

**4. Install TEPUB**

```bash
git clone https://github.com/xiaolai/tepub.git
cd tepub
~/.pyenv/versions/3.11.13/bin/python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Done! Continue to [Test Your Installation](#test-your-installation).

---

### Installing on Linux (Ubuntu/Debian)

#### Step-by-Step:

**1. Update Your System**

Open Terminal and run:

```bash
sudo apt-get update
sudo apt-get upgrade
```

Enter your password when asked.

**2. Install Helper Programs**

```bash
sudo apt-get install -y libxml2-dev libxslt-dev ffmpeg build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev wget curl git
```

**3. Install pyenv (Python Manager)**

```bash
curl https://pyenv.run | bash
```

Add it to your shell:

```bash
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
source ~/.bashrc
```

Install Python:

```bash
pyenv install 3.11.13
```

**4. Install TEPUB**

```bash
git clone https://github.com/xiaolai/tepub.git
cd tepub
~/.pyenv/versions/3.11.13/bin/python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Done! Continue to [Test Your Installation](#test-your-installation).

---

### Installing on Windows

You have two options for Windows:

#### Option 1: Using WSL (Recommended – Easier and More Reliable)

WSL lets you run Linux inside Windows. This is the easiest way to use TEPUB on Windows.

**1. Install WSL**

Open PowerShell as Administrator (right-click → "Run as administrator") and type:

```powershell
wsl --install
```

**2. Restart your computer** when it asks

**3. Open "Ubuntu"** from your Start menu (it was installed with WSL)

**4. Follow the Linux installation steps** above inside Ubuntu

#### Option 2: Native Windows (More Complex)

**1. Install Python**
- Go to [python.org/downloads](https://www.python.org/downloads/)
- Download Python 3.11.13
- Run the installer
- ✅ **IMPORTANT**: Check "Add Python to PATH" before clicking Install

**2. Install Git**
- Go to [git-scm.com](https://git-scm.com/download/win)
- Download and install Git
- Use default settings

**3. Install Build Tools**
- Go to [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
- Download and run the installer
- Select "Desktop development with C++"
- Click Install (this takes 10-20 minutes)

**4. Install FFmpeg**
- Go to [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
- Download the Windows build
- Extract the ZIP file to `C:\ffmpeg`
- Add `C:\ffmpeg\bin` to your PATH:
  1. Press Windows key, type "environment variables"
  2. Click "Edit system environment variables"
  3. Click "Environment Variables" button
  4. Under "System variables", find "Path", click "Edit"
  5. Click "New" and add `C:\ffmpeg\bin`
  6. Click OK on all windows

**5. Install TEPUB**

Open PowerShell and run:

```powershell
git clone https://github.com/xiaolai/tepub.git
cd tepub
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

Done! Continue to [Test Your Installation](#test-your-installation).

---

## Test Your Installation

After installing, let's make sure everything works:

### 1. Activate the Environment

**Mac/Linux:**
```bash
source .venv/bin/activate
```

**Windows:**
```powershell
.venv\Scripts\activate
```

You should see `(.venv)` appear at the start of your command line.

### 2. Check TEPUB Works

Type this command:

```bash
tepub --version
```

You should see something like:
```
tepub, version 0.1.0
```

### 3. Check Python Version

```bash
python --version
```

You should see:
```
Python 3.11.13
```
(or any version starting with 3.11 or 3.12)

### 4. Check FFmpeg (for Audiobooks)

```bash
ffmpeg -version
```

You should see version information for ffmpeg.

### 5. Run the Tests

```bash
pytest
```

You should see lots of green text and "passed" messages. If you see errors, something went wrong – see the [Troubleshooting](#troubleshooting) section below.

**If all these work, congratulations! TEPUB is installed!** 🎉

---

## Set Up Your Translation Service

To translate books, you need an account with a translation service. Here's how:

### Choose a Service

**For beginners, we recommend OpenAI:**

1. Go to [platform.openai.com](https://platform.openai.com/)
2. Create an account
3. Add $5-10 to your account (this translates many books)
4. Go to API Keys section
5. Click "Create new secret key"
6. Copy the key (starts with `sk-`)

**OpenAI provides:**
- Translation via ChatGPT (~$0.50-$2.00 per book)
- Premium audiobook voices (~$11-22 per 300-page book)

**Other options:**
- [Anthropic Claude](https://console.anthropic.com/) – Great for literature translation
- [Ollama](https://ollama.com/) – Free local translation (no internet needed)
- **Edge TTS** – Free audiobooks with 57+ voices (no API key needed, installed by default)

### Tell TEPUB About Your Key

Create a file named `.env` in the tepub folder:

**Mac/Linux:**
```bash
cd tepub  # if you're not already there
echo 'OPENAI_API_KEY=sk-your-actual-key-here' > .env
```

**Windows:**
Create a file called `.env` in the `tepub` folder using Notepad and add this line:
```
OPENAI_API_KEY=sk-your-actual-key-here
```

Replace `sk-your-actual-key-here` with your real API key.

### Try Translating a Book

```bash
tepub extract yourbook.epub
tepub translate yourbook.epub --to "Simplified Chinese"
```

If it works without errors, you're all set!

---

## Troubleshooting

### "Command not found: tepub"

**Problem:** Your computer can't find TEPUB.

**Solution:** Make sure the virtual environment is activated:

```bash
# Mac/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

You should see `(.venv)` appear in your terminal. If this still doesn't work:

```bash
pip install -e .[dev]
```

### "lxml installation failed" Error

**Problem:** Missing helper programs for reading book files.

**Solution:**

**Mac:**
```bash
brew install libxml2 libxslt
```

**Ubuntu/Linux:**
```bash
sudo apt-get install libxml2-dev libxslt-dev
```

Then try installing again:
```bash
pip install -e .[dev]
```

### "ffmpeg not found" Error

**Problem:** FFmpeg isn't installed (needed for audiobooks).

**Solution:**

**Mac:**
```bash
brew install ffmpeg
```

**Ubuntu/Linux:**
```bash
sudo apt-get install ffmpeg
```

**Windows:**
- Download from [ffmpeg.org](https://ffmpeg.org/download.html)
- Extract to `C:\ffmpeg`
- Add `C:\ffmpeg\bin` to PATH (see Windows installation above)

### "Python version too old" Error

**Problem:** You have Python 2.x or 3.9 or older.

**Solution:** Install Python 3.11 using the instructions above for your operating system.

### "Permission denied" Error During Install

**Problem:** The installer doesn't have permission to run.

**Solution:**

**Mac/Linux:**
```bash
chmod +x install.sh
./install.sh
```

**Don't use `sudo`** – this can cause problems.

### "API key not working" Error

**Problem:** TEPUB can't find or use your API key.

**Solution:**

1. Check the key is set:
```bash
cat .env
```

2. Make sure there are no extra spaces or quotes
3. The file should look exactly like this:
```
OPENAI_API_KEY=sk-proj-xxxxxxxx
```

4. Make sure you're in the tepub folder when running commands

### Everything Else

If you're still stuck:

1. **Check existing solutions**: [GitHub Issues](https://github.com/xiaolai/tepub/issues)
2. **Ask for help**: Create a new GitHub issue with:
   - What operating system you're using (Mac/Windows/Linux)
   - What you tried to do
   - What error message you got (copy the whole message)
   - The output of `python --version`

---

## What to Do Next

Now that TEPUB is installed:

1. **Read the Quick Start**: See [README.md](README.md) for how to translate your first book
2. **Set up preferences**: Copy `config.example.yaml` to `~/.tepub/config.yaml` and customize it
3. **Try it out**: Start with a short EPUB file to test everything works

### Activate TEPUB Each Time

**Important:** Each time you open a new Terminal window, you need to activate TEPUB:

```bash
cd tepub
source .venv/bin/activate    # Mac/Linux
.venv\Scripts\activate       # Windows
```

You'll know it's active when you see `(.venv)` at the start of your command line.

### Optional: Make Activation Easier

**Mac/Linux** – Add this to your `~/.zshrc` or `~/.bashrc`:

```bash
alias tepub-activate='cd ~/tepub && source .venv/bin/activate'
```

Now you can just type `tepub-activate` instead of remembering the path.

---

**Happy translating!** 📚

If you get stuck, remember: the installation is the hardest part. Once it's working, using TEPUB is much easier!
