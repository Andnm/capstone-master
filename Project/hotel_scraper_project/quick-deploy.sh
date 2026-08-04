#!/bin/bash
# =====================================================
# QUICK DEPLOY SCRIPT - HOTEL SCRAPER PROJECT
# =====================================================
# This script automates the deployment process
# Run with: ./quick-deploy.sh
# =====================================================

set -e

echo "=========================================="
echo "  🚀 Hotel Scraper - Quick Deploy"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored messages
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Step 1: Check prerequisites
print_status "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install Docker first."
    exit 1
fi

if ! docker compose version &> /dev/null; then
    print_error "Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

print_success "Docker and Docker Compose are installed"

# Step 2: Check .env file
echo ""
print_status "Checking environment configuration..."

if [ ! -f .env ]; then
    print_warning ".env file not found. Creating from .env.example..."
    
    if [ -f .env.example ]; then
        cp .env.example .env
        print_success ".env file created"
        print_warning "⚠️  IMPORTANT: Please edit .env file and set your passwords!"
        print_warning "   Run: nano .env"
        echo ""
        read -p "Press Enter after you've configured .env file..."
    else
        print_error ".env.example not found. Cannot create .env"
        exit 1
    fi
else
    print_success ".env file exists"
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Step 3: Pull latest code (optional)
echo ""
print_status "Checking for code updates..."
git pull origin refactor-code 2>/dev/null || print_warning "Git pull skipped or failed (this is OK)"

# Step 4: Stop existing containers
echo ""
print_status "Stopping existing containers (if any)..."
docker compose down 2>/dev/null || true
print_success "Containers stopped"

# Step 5: Build images
echo ""
print_status "Building Docker images..."
print_warning "This may take 5-10 minutes on first build..."
docker compose build

if [ $? -eq 0 ]; then
    print_success "Docker images built successfully"
else
    print_error "Failed to build Docker images"
    exit 1
fi

# Step 6: Start services
echo ""
print_status "Starting services..."
docker compose up -d

if [ $? -eq 0 ]; then
    print_success "Services started successfully"
else
    print_error "Failed to start services"
    exit 1
fi

# Step 7: Wait for services to be ready
echo ""
print_status "Waiting for services to be ready..."
echo ""

# Wait for database
print_status "Waiting for database to be ready (max 60s)..."
for i in {1..30}; do
    if docker compose exec -T db mysqladmin ping -h localhost -u${DB_USER} -p${DB_PASSWORD} &> /dev/null; then
        print_success "Database is ready!"
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

# Wait for backend
print_status "Waiting for backend to be ready (max 30s)..."
for i in {1..15}; do
    if curl -s http://localhost:8000/docs > /dev/null 2>&1; then
        print_success "Backend is ready!"
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

# Step 8: Show status
echo ""
print_status "Container status:"
docker compose ps

# Step 9: Verify database
echo ""
print_status "Verifying database..."
TABLES=$(docker compose exec -T db mysql -u${DB_USER} -p${DB_PASSWORD} ${DB_NAME} -e "SHOW TABLES;" --skip-column-names 2>/dev/null | wc -l)

if [ "$TABLES" -gt 0 ]; then
    print_success "Database initialized with $TABLES tables"
    echo ""
    print_status "Database tables:"
    docker compose exec -T db mysql -u${DB_USER} -p${DB_PASSWORD} ${DB_NAME} -e "SHOW TABLES;" 2>/dev/null
else
    print_warning "Database is empty. Tables might not be created yet."
fi

# Step 10: Show logs
echo ""
print_status "Recent backend logs:"
docker compose logs --tail=20 backend

# Step 11: Success message
echo ""
echo "=========================================="
print_success "🎉 Deployment Complete!"
echo "=========================================="
echo ""
echo "📊 Access your application:"
echo "   - Frontend:  http://localhost (or http://your-domain.com)"
echo "   - Backend:   http://localhost:8000"
echo "   - API Docs:  http://localhost:8000/docs"
echo ""
echo "🔧 Useful commands:"
echo "   - View logs:       docker compose logs -f"
echo "   - View backend:    docker compose logs -f backend"
echo "   - View database:   docker compose logs -f db"
echo "   - Stop services:   docker compose stop"
echo "   - Restart:         docker compose restart"
echo "   - Update code:     git pull && docker compose build && docker compose up -d"
echo ""
echo "🗄️  Database commands:"
echo "   - Access MySQL:    docker compose exec db mysql -u${DB_USER} -p${DB_PASSWORD} ${DB_NAME}"
echo "   - Show tables:     docker compose exec db mysql -u${DB_USER} -p${DB_PASSWORD} ${DB_NAME} -e 'SHOW TABLES;'"
echo "   - Backup DB:       docker compose exec db mysqldump -u${DB_USER} -p${DB_PASSWORD} ${DB_NAME} > backup.sql"
echo ""
print_warning "If frontend shows 'Cannot connect to backend', wait 30-60 seconds for full initialization"
echo ""
