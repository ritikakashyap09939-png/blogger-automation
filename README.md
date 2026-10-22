# Blogger Auto-Post (Daily)

Ritika Rajput ke portfolio ke liye **roz** (Tuesday se Sunday samet har din)
ek original blog post generate karke, usse related photo lagakar, Blogger par
publish karne wali automation. Sab kuch GitHub Actions par chalta hai.

Portfolio: https://ritika-rajput-portfolio.vercel.app
Blog: https://ritikarajput-dev.blogspot.com ("Ritika Rajput | Web Dev Notes")

---

## 1. Ye kaise kaam karta hai (How it works)

Roz 10:00 AM IST par GitHub Actions ye pipeline chalata hai:

1. **generate_post.py** — `skills.txt` se ek skill rotate karke chunta hai,
   Gemini API se us skill par ek 700-1000 word ka post banata hai (JSON mein:
   title, labels, image_query, body). Har post mein portfolio ke 1-2 natural
   links hote hain, anchor text har baar alag.
2. **fetch_image.py** — Openverse (free, bina API key) se topic-related
   landscape photo dhoondta hai aur post ke sabse upar `<img>` laga deta hai
   (Blogger isi ko thumbnail banata hai). Creator + license ka credit caption
   bhi lagta hai. HTTP 429 (rate limit) par wait karke retry karta hai.
3. **post_to_blogger.py** — Blogger API par post bhejta hai. **Default draft.**
   Live sirf tab jab `POST_LIVE=true` ho. Duplicate title skip karta hai.

Har run ke baad `used_topics.txt`, `anchors.txt` aur `used_images.txt` repo mein
commit ho jaate hain, taaki koi cheez repeat na ho.

Extra safety:
- **Model fallback** — `GEMINI_MODEL` set nahi hai ya model 404 de, to script
  khud Gemini ListModels se ek available **flash** model choose kar leti hai
  (highest version) aur print karti hai.
- **Photo na mile to post rukta nahi** — Openverse se koi image na mile to
  `fetch_image.py` sirf warning print karta hai aur post bina image ke
  publish ho jaati hai (job fail nahi hota).
- **No fabricated claims** — draft mein "my client", "for a client",
  "I helped a", "increased ... by NN%" jaise patterns mile to post reject
  hokar dobara generate hota hai (max 3 attempts).

## 2. Folder structure

```
.github/workflows/post.yml   daily pipeline (cron + manual)
scripts/generate_post.py     Gemini se post banata hai
scripts/fetch_image.py       Openverse se photo lagata hai (no API key)
scripts/post_to_blogger.py   Blogger par post karta hai
scripts/get_refresh_token.py one-time token (local)
posts/                       aaj ka pending post (run ke baad published/)
posts/published/             post ho chuki files
skills.txt                   portfolio se nikaali gayi skills (EDIT MAT karein)
used_topics.txt              skill + title log (auto)
anchors.txt                  anchor text log (auto)
used_images.txt              Openverse image id log (auto)
tests/test_front_matter.py   local unit test (no network, mocked)
requirements.txt .env.example .gitignore README.md
```

## 3. Manual setup checklist (ye ek baar karna hai)

1. **Google Cloud project + Blogger API**
   - console.cloud.google.com par naya project banayein.
   - **Blogger API v3** enable karein.
   - **OAuth consent screen** (External) banayein, scope add karein:
     `https://www.googleapis.com/auth/blogger`.
   - **Credentials → Create OAuth client ID → Desktop app**. Client ID aur
     Secret copy karein.

2. **Consent screen ko Production par publish karein** (Testing mode ke tokens
   ~7 din mein expire hote hain → `invalid_grant`).

3. **Refresh token banayein**
   - `copy .env.example .env`
   - `.env` mein `BLOGGER_CLIENT_ID` aur `BLOGGER_CLIENT_SECRET` daalein.
   - `pip install -r requirements.txt`
   - `python scripts/get_refresh_token.py` → terminal par token print hoga.
   - Us token ko `.env` ke `BLOGGER_REFRESH_TOKEN` mein daalein.

4. **Blog ID** — Blogger dashboard ke URL mein `blogID=XXXX` hota hai.

5. **Gemini API key** — aistudio.google.com se `GEMINI_API_KEY` lein.
   (Photo ke liye koi key nahi chahiye — Openverse anonymous chalta hai.)

6. **Private GitHub repo banayein** aur code push karein (commands neeche).

7. **GitHub Secrets add karein** (Settings → Secrets and variables → Actions):
   - `BLOGGER_CLIENT_ID`
   - `BLOGGER_CLIENT_SECRET`
   - `BLOGGER_REFRESH_TOKEN`
   - `BLOGGER_BLOG_ID`
   - `GEMINI_API_KEY`

8. **Optional variable**: `GEMINI_MODEL`. Agar ye empty hai ya model 404
   kare, to script ListModels se available flash model khud choose kar leti hai.

## 4. Test kaise karein (How to test)

Pehle dry run karein, phir draft:

1. GitHub → **Actions → Daily Blogger Auto-Post → Run workflow**.
2. `as_draft = true` (default), `dry_run = true`.
   - `dry_run` sirf posting step ko rokta hai (generate + image phir bhi hote
     hain), aur **commit step bhi skip** ho jaata hai - test run mein koi
     generated file ya log repo mein commit nahi hoti.
3. Sab theek lage to `dry_run = false` ke saath dobara chalayein — ye **draft**
   banayega (live nahi).
4. Blogger dashboard → Drafts mein post check karein.

Local test:

```powershell
# Windows PowerShell
$env:DRY_RUN="true"; python scripts/post_to_blogger.py
```

Unit test (poori tarah mocked, koi network call nahi):

```powershell
python -m unittest discover -s tests -v
```

## 5. Live kaise jaayein (How to go live)

Scheduled runs by default **draft** bhejte hain. Live karne ke liye:

- Repo → **Settings → Secrets and variables → Actions → Variables tab**
- Naya variable: `POST_LIVE` = `true`

Ab daily runs live publish karenge. Draft-only par wapas jaane ke liye
`POST_LIVE` ko `false` karein ya delete kar dein.

## 6. Kaise pause karein (How to pause)

- **Sabse aasan**: Repo → **Actions tab → Daily Blogger Auto-Post → "..." →
  Disable workflow**.
- Ya workflow file se `schedule:` block comment kar dein.
- Ya `POST_LIVE` hataa dein (posts draft banenge, rukenge nahi).

## 7. Troubleshooting

| Problem | Matlab / Fix |
| --- | --- |
| `invalid_grant` | Consent screen Testing mode mein (tokens ~7 din chalti hain) ya token revoke. Production par publish karein, phir `get_refresh_token.py` dobara chalayein aur secret update karein. |
| `403` (Blogger) | Token mein Blogger scope nahi, ya Blogger API enable nahi, ya aap blog owner nahi. Sahi scope ke saath token dobara banayein. |
| `Openverse HTTP 429` | Rate limit — script khud wait karke retry karta hai (max 3 retries, backoff ke saath). Baar baar aaye to thodi der baad chalayein. |
| `no unused Openverse photo` | Saare results already used ya bina image url ke the. **Post fail nahi hota** — script warning print karke post ko bina image ke publish karne deti hai. |
| `GEMINI_API_KEY is not set` | Secret add nahi hua ya naam galat hai. |
| `could not generate a valid post` | Gemini ne 3 baar invalid output diya (ya draft mein fabricated personal-claim pattern mila). `GEMINI_MODEL` change karein ya prompt check karein. |
| `model not found` / 404 | Script khud ListModels se flash model choose kar leti hai; koi na mile to `GEMINI_MODEL` variable set karein. |
| Push permission error | Repo settings → Actions → General → Workflow permissions → "Read and write permissions". |
| Workflows paused (60 din inactivity) | GitHub 60 din repo inactivity par scheduled workflows pause kar deta hai. Ek commit karein ya manually chalayein. |
| `No pending posts` | Aaj ka post already publish ho chuka hai (ya generate fail hua). |

## 8. Private GitHub repo banana aur push karna

```bash
cd "blogger-automation"
git init
git add .
git commit -m "Initial commit: daily Blogger auto-post"
git branch -M main
```

Phir GitHub par ek **private** repo banayein (naam suggestion:
`blogger-automation`), aur:

```bash
git remote add origin https://github.com/<your-username>/blogger-automation.git
git push -u origin main
```

Uske baad upar diye gaye GitHub Secrets add karein.

---

### Owner

Ritika Rajput — [Portfolio](https://ritika-rajput-portfolio.vercel.app)
