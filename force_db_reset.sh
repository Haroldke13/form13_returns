#!/bin/bash
# Force database reset and user reseeding for Docker deployment
# This script clears all markers and forces a complete reset

echo "🔄 Forcing database reset and user reseeding..."

# Remove bootstrap markers to force database recreation
rm -f /app/.bootstrap_done
rm -f /app/.user4_reset_done

echo "✅ Database markers cleared"
echo "📝 Next Docker deployment will:"
echo "   - Recreate database from scratch"
echo "   - Reseed all users with current .env values"
echo "   - Reset user4 credentials"
echo "   - Apply all latest code changes"
echo "   - Clean up marker files after successful operations"

echo "🚀 Ready for deployment!"