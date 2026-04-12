#!/bin/bash
# WordPress セットアップスクリプト（Ubuntu + Nginx + PHP + MySQL）
# 対象: ai-bento.com / さくらVPS
#
# 実行方法:
#   bash setup_wp_vps.sh

set -e

DOMAIN="ai-bento.com"
WP_DIR="/var/www/${DOMAIN}"
DB_NAME="wordpress_aibento"
DB_USER="wp_aibento"
DB_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 20)
DB_ROOT_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 20)
PHP_VER="8.2"

echo "================================================"
echo " WordPress セットアップ開始: ${DOMAIN}"
echo "================================================"

# ────────────────────────────────────────
# 1. システム更新
# ────────────────────────────────────────
echo ""
echo "[1/7] システム更新..."
apt-get update -y
apt-get upgrade -y

# ────────────────────────────────────────
# 2. Nginx インストール
# ────────────────────────────────────────
echo ""
echo "[2/7] Nginx インストール..."
apt-get install -y nginx
systemctl enable nginx
systemctl start nginx

# ────────────────────────────────────────
# 3. PHP インストール
# ────────────────────────────────────────
echo ""
echo "[3/7] PHP ${PHP_VER} インストール..."
apt-get install -y software-properties-common
add-apt-repository -y ppa:ondrej/php
apt-get update -y
apt-get install -y \
    php${PHP_VER}-fpm \
    php${PHP_VER}-mysql \
    php${PHP_VER}-xml \
    php${PHP_VER}-mbstring \
    php${PHP_VER}-curl \
    php${PHP_VER}-zip \
    php${PHP_VER}-gd \
    php${PHP_VER}-intl \
    php${PHP_VER}-imagick

systemctl enable php${PHP_VER}-fpm
systemctl start php${PHP_VER}-fpm

# ────────────────────────────────────────
# 4. MySQL インストール
# ────────────────────────────────────────
echo ""
echo "[4/7] MySQL インストール..."
apt-get install -y mysql-server

# MySQL セキュア設定
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '${DB_ROOT_PASS}';"
mysql -u root -p"${DB_ROOT_PASS}" -e "DELETE FROM mysql.user WHERE User='';"
mysql -u root -p"${DB_ROOT_PASS}" -e "DROP DATABASE IF EXISTS test;"
mysql -u root -p"${DB_ROOT_PASS}" -e "FLUSH PRIVILEGES;"

# WordPress 用 DB 作成
mysql -u root -p"${DB_ROOT_PASS}" -e "CREATE DATABASE ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p"${DB_ROOT_PASS}" -e "CREATE USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';"
mysql -u root -p"${DB_ROOT_PASS}" -e "GRANT ALL PRIVILEGES ON ${DB_NAME}.* TO '${DB_USER}'@'localhost';"
mysql -u root -p"${DB_ROOT_PASS}" -e "FLUSH PRIVILEGES;"

echo "  DB名: ${DB_NAME}"
echo "  DBユーザー: ${DB_USER}"
echo "  DBパスワード: ${DB_PASS}"

# ────────────────────────────────────────
# 5. WordPress インストール
# ────────────────────────────────────────
echo ""
echo "[5/7] WordPress インストール..."
mkdir -p "${WP_DIR}"
cd /tmp
wget -q https://wordpress.org/latest.tar.gz
tar xzf latest.tar.gz
cp -r wordpress/. "${WP_DIR}/"
rm -rf wordpress latest.tar.gz

# wp-config.php 設定
cp "${WP_DIR}/wp-config-sample.php" "${WP_DIR}/wp-config.php"
sed -i "s/database_name_here/${DB_NAME}/" "${WP_DIR}/wp-config.php"
sed -i "s/username_here/${DB_USER}/" "${WP_DIR}/wp-config.php"
sed -i "s/password_here/${DB_PASS}/" "${WP_DIR}/wp-config.php"

# セキュリティキー生成・設定
KEYS=$(curl -s https://api.wordpress.org/secret-key/1.1/salt/)
# wp-config の既存キー部分を置換
python3 - <<PYEOF
import re
with open("${WP_DIR}/wp-config.php", "r") as f:
    content = f.read()
keys = """${KEYS}"""
# define('AUTH_KEY'... から define('NONCE_SALT'... の8行を置換
content = re.sub(
    r"define\('AUTH_KEY'.*?define\('NONCE_SALT'.*?\);",
    keys,
    content,
    flags=re.DOTALL
)
with open("${WP_DIR}/wp-config.php", "w") as f:
    f.write(content)
PYEOF

# パーミッション設定
chown -R www-data:www-data "${WP_DIR}"
chmod -R 755 "${WP_DIR}"

# ────────────────────────────────────────
# 6. Nginx 設定
# ────────────────────────────────────────
echo ""
echo "[6/7] Nginx 設定..."
cat > /etc/nginx/sites-available/${DOMAIN} <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};
    root ${WP_DIR};
    index index.php index.html;

    # WordPress パーマリンク対応
    location / {
        try_files \$uri \$uri/ /index.php?\$args;
    }

    # PHP 処理
    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php${PHP_VER}-fpm.sock;
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        include fastcgi_params;
    }

    # 静的ファイルキャッシュ
    location ~* \.(css|js|jpg|jpeg|png|gif|ico|svg|woff2?)$ {
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    # セキュリティ
    location ~ /\.ht {
        deny all;
    }

    client_max_body_size 64M;
}
EOF

ln -sf /etc/nginx/sites-available/${DOMAIN} /etc/nginx/sites-enabled/${DOMAIN}
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# ────────────────────────────────────────
# 7. SSL (Let's Encrypt)
# ────────────────────────────────────────
echo ""
echo "[7/7] SSL 証明書取得..."
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d ${DOMAIN} -d www.${DOMAIN} \
    --non-interactive --agree-tos \
    --email admin@${DOMAIN} \
    --redirect

systemctl reload nginx

# ────────────────────────────────────────
# 完了メッセージ
# ────────────────────────────────────────
echo ""
echo "================================================"
echo " セットアップ完了！"
echo "================================================"
echo ""
echo " サイトURL  : https://${DOMAIN}"
echo " WP管理画面 : https://${DOMAIN}/wp-admin"
echo ""
echo " DB名       : ${DB_NAME}"
echo " DBユーザー : ${DB_USER}"
echo " DBパスワード: ${DB_PASS}"
echo " DB rootパスワード: ${DB_ROOT_PASS}"
echo ""
echo "【次のステップ】"
echo " 1. https://${DOMAIN}/wp-admin にアクセスしてWordPressの初期設定を完了"
echo " 2. 管理者ユーザーを作成"
echo " 3. アプリケーションパスワードを発行（ユーザー → プロフィール → アプリケーションパスワード）"
echo " 4. 以下を .env に追加:"
echo "    WP_SITE_URL=https://${DOMAIN}"
echo "    WP_USERNAME=（管理者ユーザー名）"
echo "    WP_APP_PASSWORD=（アプリケーションパスワード）"
echo ""
echo " ※ DBパスワードは必ずメモしてください"
