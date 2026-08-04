#!/bin/bash
# =====================================================
# DATABASE INITIALIZATION SCRIPT
# =====================================================
# This script initializes the database with setup.sql
# It ensures the database exists and tables are created
# =====================================================

set -e

# Create log directory
mkdir -p /var/log

# Redirect all output to log file
exec > >(tee /var/log/init-db.log) 2>&1

echo "=========================================="
echo "Starting Database Initialization..."
echo "========================================="

# Wait for MySQL to be ready
echo "⏳ Waiting for MySQL to be ready..."
sleep 5

# Function to wait for MySQL
wait_for_mysql() {
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if mysql --skip-ssl -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" -e "SELECT 1" > /dev/null 2>&1; then
            echo "✅ MySQL is ready!"
            return 0
        fi
        echo "   Attempt $attempt/$max_attempts - MySQL not ready yet..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo "❌ MySQL failed to start after $max_attempts attempts"
    return 1
}

# Wait for MySQL to be ready
if ! wait_for_mysql; then
    exit 1
fi

echo ""
echo "📊 Checking database status..."

# Check if database exists
DB_EXISTS=$(mysql --skip-ssl -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" \
    -e "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='${DB_NAME}';" \
    --skip-column-names)

if [ -z "$DB_EXISTS" ]; then
    echo "⚠️  Database '${DB_NAME}' does not exist. Creating..."
    mysql --skip-ssl -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" \
        -e "CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    echo "✅ Database created successfully!"
else
    echo "ℹ️  Database '${DB_NAME}' already exists"
fi

echo ""
echo "📝 Checking tables..."

# Check if tables exist
TABLE_COUNT=$(mysql --skip-ssl -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" \
    -e "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='${DB_NAME}';" \
    --skip-column-names)

if [ "$TABLE_COUNT" -eq "0" ]; then
    echo "⚠️  No tables found. Running setup.sql..."
    
    if [ -f "/app/app/database/setup.sql" ]; then
        mysql --skip-ssl -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" < /app/app/database/setup.sql
        echo "✅ Database schema created successfully!"
    else
        echo "❌ setup.sql file not found!"
        exit 1
    fi
else
    echo "ℹ️  Found $TABLE_COUNT existing tables"
    echo "🔄 Skipping schema creation (tables already exist)"
fi

echo ""
echo "📋 Database Tables:"
mysql --skip-ssl -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" \
    -e "SHOW TABLES;"

echo ""
echo "=========================================="
echo "✅ Database Initialization Complete!"
echo "=========================================="
