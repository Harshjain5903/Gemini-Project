#!/usr/bin/env bash
set -e

# Install backend Python dependencies
pip install -r risk_aware_routing/requirements.txt

# Pre-download Chicago street network graph (cached as GraphML for fast startup)
# This runs during the build step which has a longer timeout than app startup
if [ ! -f output_chicago/chicago_graph.graphml ]; then
  echo "Downloading Chicago street network (first time only)..."
  cd risk_aware_routing
  python -c "from routing_engine import RoutingEngine; RoutingEngine('Chicago, Illinois, USA', cache_path='../output_chicago/chicago_graph.graphml')"
  cd ..
  echo "Graph cached successfully."
else
  echo "Chicago graph cache found, skipping download."
fi

# Build frontend
cd frontend
npm install
npm run build
cd ..
