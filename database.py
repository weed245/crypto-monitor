import aiosqlite


DATABASE_FILE = "monitor.db"


async def init_database():
    async with aiosqlite.connect(DATABASE_FILE) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                title TEXT,
                content TEXT,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS monitored_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                enabled INTEGER DEFAULT 1
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        await db.commit()


async def alert_exists(alert_id):
    async with aiosqlite.connect(DATABASE_FILE) as db:

        cursor = await db.execute(
            "SELECT 1 FROM alerts WHERE alert_id = ? LIMIT 1",
            (alert_id,)
        )

        result = await cursor.fetchone()

        return result is not None


async def save_alert(
    alert_id,
    alert_type,
    title="",
    content="",
    url=""
):
    async with aiosqlite.connect(DATABASE_FILE) as db:

        try:

            await db.execute("""
                INSERT INTO alerts
                (
                    alert_id,
                    alert_type,
                    title,
                    content,
                    url
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                alert_id,
                alert_type,
                title,
                content,
                url
            ))

            await db.commit()

            return True

        except aiosqlite.IntegrityError:

            return False


async def add_account(username):

    username = (
        username
        .replace("@", "")
        .strip()
        .lower()
    )

    async with aiosqlite.connect(DATABASE_FILE) as db:

        try:

            await db.execute("""
                INSERT INTO monitored_accounts
                (username, enabled)
                VALUES (?, 1)
            """, (username,))

            await db.commit()

            return True

        except aiosqlite.IntegrityError:

            return False


async def remove_account(username):

    username = (
        username
        .replace("@", "")
        .strip()
        .lower()
    )

    async with aiosqlite.connect(DATABASE_FILE) as db:

        cursor = await db.execute("""
            DELETE FROM monitored_accounts
            WHERE username = ?
        """, (username,))

        await db.commit()

        return cursor.rowcount > 0


async def get_accounts():

    async with aiosqlite.connect(DATABASE_FILE) as db:

        cursor = await db.execute("""
            SELECT username
            FROM monitored_accounts
            WHERE enabled = 1
            ORDER BY username
        """)

        rows = await cursor.fetchall()

        return [row[0] for row in rows]


async def set_setting(key, value):

    async with aiosqlite.connect(DATABASE_FILE) as db:

        await db.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
        """, (key, value))

        await db.commit()


async def get_setting(key):

    async with aiosqlite.connect(DATABASE_FILE) as db:

        cursor = await db.execute("""
            SELECT value
            FROM settings
            WHERE key = ?
        """, (key,))

        row = await cursor.fetchone()

        if row:
            return row[0]

        return None
