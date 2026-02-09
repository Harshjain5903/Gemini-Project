import axios from 'axios';

// Create API connection with timeout
const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' }
});

// Endpoint: Get Comparison between Fast vs Safe Route
export const compareRoutes = async (start, end, beta, hour, isWeekend) => {
  try {
    const response = await api.post('/compare-routes', {
      start,
      end,
      beta,
      hour,
      is_weekend: isWeekend
    });
    return response.data;
  } catch (error) {
    console.error("API Connection Error:", error);
    throw error;
  }
};