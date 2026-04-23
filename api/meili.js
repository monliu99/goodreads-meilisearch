export default async function handler(request, response) {
  if (request.method !== 'POST') {
    return response.status(405).json({ error: 'Method not allowed' });
  }

  const MEILI_URL = process.env.MEILI_URL;
  const MEILI_SEARCH_KEY = process.env.MEILI_SEARCH_KEY;

  if (!MEILI_URL || !MEILI_SEARCH_KEY) {
    return response.status(500).json({ error: 'Search service not configured' });
  }

  const url = new URL(request.url, `https://${request.headers.host}`);
  const meiliPath = url.pathname.replace(/^\/api\/meili\/?/, '');

  const allowed = [
    /^indexes\/[^/]+\/search$/,
    /^indexes\/[^/]+\/facet-search$/,
    /^multi-search$/,
  ];
  if (!allowed.some((p) => p.test(meiliPath))) {
    return response.status(400).json({ error: 'Invalid search path' });
  }

  const meiliUrl = `${MEILI_URL.replace(/\/$/, '')}/${meiliPath}`;

  try {
    const meiliResponse = await fetch(meiliUrl, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${MEILI_SEARCH_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request.body),
    });

    const data = await meiliResponse.json();
    return response.status(meiliResponse.status).json(data);
  } catch (_error) {
    return response.status(502).json({ error: 'Search service unreachable' });
  }
}
