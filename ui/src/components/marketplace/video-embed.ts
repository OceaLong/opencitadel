export type VideoEmbed =
  | { embeddable: true; provider: "youtube" | "bilibili"; embedUrl: string }
  | { embeddable: false; reason: string };

const YOUTUBE_ID_PATTERN = /^[a-zA-Z0-9_-]{11}$/;

function extractYouTubeId(url: URL): string | null {
  const host = url.hostname.replace(/^www\./, "");

  if (host === "youtu.be") {
    const id = url.pathname.slice(1).split("/")[0];
    return id && YOUTUBE_ID_PATTERN.test(id) ? id : null;
  }

  if (host === "youtube.com" || host === "m.youtube.com") {
    const fromQuery = url.searchParams.get("v");
    if (fromQuery && YOUTUBE_ID_PATTERN.test(fromQuery)) {
      return fromQuery;
    }

    const embedMatch = url.pathname.match(/\/embed\/([a-zA-Z0-9_-]{11})/);
    if (embedMatch?.[1]) {
      return embedMatch[1];
    }

    const shortsMatch = url.pathname.match(/\/shorts\/([a-zA-Z0-9_-]{11})/);
    if (shortsMatch?.[1]) {
      return shortsMatch[1];
    }
  }

  return null;
}

function extractBilibiliBvid(url: URL): string | null {
  const host = url.hostname.replace(/^www\./, "");
  if (!host.endsWith("bilibili.com")) {
    return null;
  }

  const match = url.pathname.match(/\/video\/(BV[0-9A-Za-z]+)/i);
  return match?.[1]?.toUpperCase() ?? null;
}

export function getVideoEmbed(url: string): VideoEmbed {
  try {
    const parsed = new URL(url);

    const youtubeId = extractYouTubeId(parsed);
    if (youtubeId) {
      return {
        embeddable: true,
        provider: "youtube",
        embedUrl: `https://www.youtube.com/embed/${youtubeId}`,
      };
    }

    const bvid = extractBilibiliBvid(parsed);
    if (bvid) {
      return {
        embeddable: true,
        provider: "bilibili",
        embedUrl: `https://player.bilibili.com/player.html?bvid=${bvid}&autoplay=0&high_quality=1`,
      };
    }

    return {
      embeddable: false,
      reason: "该来源不支持页内播放",
    };
  } catch {
    return {
      embeddable: false,
      reason: "链接格式无效，无法页内播放",
    };
  }
}

export function isEmbeddableVideoUrl(url: string): boolean {
  return getVideoEmbed(url).embeddable;
}
