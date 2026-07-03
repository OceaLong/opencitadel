import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

const CITADEL_PATH =
  "M4 12 H7 V9 H11 V12 H14 V9 H18 V12 H21 V9 H25 V12 H28 V28 H4 Z M13 28 V21 C13 19.3 14.3 18 16 18 C17.7 18 19 19.3 19 21 V28 Z";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "transparent",
        }}
      >
        <svg width="28" height="28" viewBox="0 0 32 32" fill="none">
          <path d={CITADEL_PATH} fill="#1e293b" fillRule="evenodd" />
        </svg>
      </div>
    ),
    { ...size },
  );
}
