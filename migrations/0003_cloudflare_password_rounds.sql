-- Cloudflare Web Crypto limits PBKDF2 iterations to 100000.
-- Re-hash the three bundled demo accounts at that supported work factor.
UPDATE users SET password_hash='pbkdf2_sha256$100000$cG9sYXJvcHMtY29tbWFuZGVy$9_2IqXpM2XAhYSX27LmJhX6jnxhkr7YgRhuG6R5Ap9k='
WHERE email='commander@polarops.local';

UPDATE users SET password_hash='pbkdf2_sha256$100000$cG9sYXJvcHMtbG9naXN0aWNz$Zox1Y31lgtZ8rcC8tAN4nyVwX9EiqAXk3Q6k3U5sKZ0='
WHERE email='logistics@polarops.local';

UPDATE users SET password_hash='pbkdf2_sha256$100000$cG9sYXJvcHMtZmllbGQ=$uBGb5BkKjiPFQTb3i-HzuDmilPeLsD6FrGc2nCBlyos='
WHERE email='field@polarops.local';
