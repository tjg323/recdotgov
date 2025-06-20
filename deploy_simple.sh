#!/bin/bash

# Simple deployment script for Google Cloud Run

echo "🚀 Simple deployment to Google Cloud Run..."

# Check if required tools are installed
command -v gcloud >/dev/null 2>&1 || { echo "❌ gcloud CLI is required. Install: https://cloud.google.com/sdk/docs/install" >&2; exit 1; }

# Get project ID
PROJECT_ID=${1:-$(gcloud config get-value project)}
SERVICE_NAME="campground-agent"
REGION="us-central1"

# Allow API keys to be passed as arguments
if [ "$2" ]; then
    export ANTHROPIC_API_KEY="$2"
    echo "📝 Using ANTHROPIC_API_KEY from argument"
fi
if [ "$3" ]; then
    export GOOGLE_API_KEY="$3"
    echo "📝 Using GOOGLE_API_KEY from argument"
fi

if [ -z "$PROJECT_ID" ]; then
    echo "❌ No project ID found. Please provide one:"
    echo "   ./deploy_simple.sh YOUR_PROJECT_ID"
    exit 1
fi

# Check API keys - also check common locations
if [ -z "$ANTHROPIC_API_KEY" ]; then
    # Try to source from common locations
    if [ -f ~/.bashrc ]; then
        source ~/.bashrc 2>/dev/null
    fi
    if [ -f ~/.zshrc ]; then
        source ~/.zshrc 2>/dev/null
    fi
fi

# Re-check after sourcing
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  ANTHROPIC_API_KEY environment variable is not set!"
    echo "   Claude Code SDK will not be available."
    echo "   To set it: export ANTHROPIC_API_KEY=your_claude_key"
fi

if [ -z "$GOOGLE_API_KEY" ]; then
    echo "⚠️  GOOGLE_API_KEY environment variable is not set!"
    echo "   Google ADK (Gemini) will not be available."
    echo "   To set it: export GOOGLE_API_KEY=your_gemini_key"
fi

# Show which keys are available
echo ""
echo "🔑 API Keys Status:"
if [ ! -z "$ANTHROPIC_API_KEY" ]; then
    echo "   ✅ ANTHROPIC_API_KEY is set (${#ANTHROPIC_API_KEY} chars)"
fi
if [ ! -z "$GOOGLE_API_KEY" ]; then
    echo "   ✅ GOOGLE_API_KEY is set (${#GOOGLE_API_KEY} chars)"
fi
echo ""

# At least one API key must be set
if [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$GOOGLE_API_KEY" ]; then
    echo "❌ At least one API key must be set!"
    echo "   Set either:"
    echo "   export ANTHROPIC_API_KEY=your_claude_key"
    echo "   export GOOGLE_API_KEY=your_gemini_key"
    exit 1
fi

echo "📋 Project: $PROJECT_ID"
echo "🌍 Region: $REGION"

# Set project
gcloud config set project $PROJECT_ID

# Enable APIs
echo "🔧 Enabling APIs..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com

# Build environment variables string
ENV_VARS=""
if [ ! -z "$ANTHROPIC_API_KEY" ]; then
    ENV_VARS="ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
fi
if [ ! -z "$GOOGLE_API_KEY" ]; then
    if [ ! -z "$ENV_VARS" ]; then
        ENV_VARS="$ENV_VARS,GOOGLE_API_KEY=$GOOGLE_API_KEY"
    else
        ENV_VARS="GOOGLE_API_KEY=$GOOGLE_API_KEY"
    fi
fi

# Show what will be deployed
echo "📦 Environment variables to be set:"
if [ ! -z "$ENV_VARS" ]; then
    echo "   $ENV_VARS" | sed 's/=[^,]*/=***/g'
else
    echo "   ⚠️  No environment variables will be set!"
fi
echo ""

# Force rebuild by using timestamped build
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
echo "🔨 Building fresh image with timestamp: $TIMESTAMP"
echo "   (This prevents using cached old images)"

# Deploy using source-based deployment (simpler)
echo "🚀 Deploying from source..."
gcloud run deploy $SERVICE_NAME \
    --source . \
    --region $REGION \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 2 \
    --timeout 900 \
    --set-env-vars "$ENV_VARS" \
    --no-use-http2 \
    --tag "build-$TIMESTAMP"

# Get URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format="value(status.url)")

echo ""
echo "✅ Deployment complete!"
echo "🌐 Your app: $SERVICE_URL"
echo ""
echo "💡 Test it with: curl $SERVICE_URL"