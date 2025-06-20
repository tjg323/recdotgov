#!/bin/bash

# Deployment script for Google Cloud Run

echo "🚀 Deploying Campground Agent to Google Cloud Run..."

# Check if required tools are installed
command -v gcloud >/dev/null 2>&1 || { echo "❌ gcloud CLI is required but not installed. Aborting." >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "❌ Docker is required but not installed. Aborting." >&2; exit 1; }

# Set variables
PROJECT_ID=${1:-"your-project-id"}
SERVICE_NAME="campground-agent"
REGION="us-central1"

if [ "$PROJECT_ID" = "your-project-id" ]; then
    echo "❌ Please provide your Google Cloud Project ID:"
    echo "   ./deploy.sh YOUR_PROJECT_ID"
    exit 1
fi

echo "📋 Using Project ID: $PROJECT_ID"
echo "🌍 Region: $REGION"
echo "🔧 Service: $SERVICE_NAME"

# Set the project
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "🔧 Enabling required APIs..."
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable containerregistry.googleapis.com

# Build and deploy using Cloud Build
echo "🏗️ Building and deploying with Cloud Build..."

# Check API keys - also check common locations
if [ -z "$ANTHROPIC_API_KEY" ] || [ -z "$GOOGLE_API_KEY" ]; then
    # Try to source from common locations
    if [ -f ~/.bashrc ]; then
        source ~/.bashrc 2>/dev/null
    fi
    if [ -f ~/.zshrc ]; then
        source ~/.zshrc 2>/dev/null
    fi
fi

# Show API key status
echo ""
echo "🔑 API Keys Status:"
if [ ! -z "$ANTHROPIC_API_KEY" ]; then
    echo "   ✅ ANTHROPIC_API_KEY is set (${#ANTHROPIC_API_KEY} chars)"
else
    echo "   ⚠️  ANTHROPIC_API_KEY not set - Claude Code SDK will not be available"
fi
if [ ! -z "$GOOGLE_API_KEY" ]; then
    echo "   ✅ GOOGLE_API_KEY is set (${#GOOGLE_API_KEY} chars)"
else
    echo "   ⚠️  GOOGLE_API_KEY not set - Google ADK (Gemini) will not be available"
fi
echo ""

# At least one API key must be set
if [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$GOOGLE_API_KEY" ]; then
    echo "❌ At least one API key must be set!"
    echo "   Set either:"
    echo "   export ANTHROPIC_API_KEY=your_api_key"
    echo "   export GOOGLE_API_KEY=your_api_key"
    exit 1
fi

# Force rebuild by using timestamped build
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
echo "🔨 Building fresh image with timestamp: $TIMESTAMP"
echo "   (This prevents using cached old images)"

# Submit build
gcloud builds submit --config cloudbuild.yaml \
    --substitutions _ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}",_GOOGLE_API_KEY="${GOOGLE_API_KEY:-}",_BUILD_TIMESTAMP="$TIMESTAMP"

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format="value(status.url)")

echo ""
echo "✅ Deployment complete!"
echo "🌐 Your app is available at: $SERVICE_URL"
echo ""
echo "🔧 To view logs:"
echo "   gcloud logs tail cloud-run-service/$SERVICE_NAME --location=$REGION"
echo ""
echo "🗑️ To delete the service:"
echo "   gcloud run services delete $SERVICE_NAME --region=$REGION"