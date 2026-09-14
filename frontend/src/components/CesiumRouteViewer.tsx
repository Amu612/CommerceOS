import React, { useEffect, useRef } from "react";
import * as Cesium from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";

export type RoutePoint = {
  lat: number;
  lng: number;
  city?: string;
  state?: string;
  label?: string;
  id?: string;
  sublabel?: string;
};

export type CesiumRouteViewerProps = {
  origin: RoutePoint | null;
  destination: RoutePoint | null;
  geometry: [number, number][]; // Array of [lng, lat]
  distanceKm?: number;
  durationFormatted?: string;
  accentColor?: string;
};

// Keyless satellite imagery (no Cesium Ion token required in the browser —
// the browser bundle must never contain an API key).
const ARCGIS_SATELLITE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

const GOOGLE_ROUTE_BLUE = "#1a73e8";

export const CesiumRouteViewer: React.FC<CesiumRouteViewerProps> = ({
  origin,
  destination,
  geometry,
  distanceKm,
  durationFormatted,
  accentColor = "#b45309",
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<Cesium.Viewer | null>(null);

  // ── Map initialization (once) ─────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

    const viewer = new Cesium.Viewer(containerRef.current, {
      animation: false,
      baseLayerPicker: false,
      baseLayer: new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({ url: ARCGIS_SATELLITE_URL, credit: "Esri, Maxar, Earthstar Geographics" }),
      ),
      fullscreenButton: false,
      geocoder: false,
      homeButton: true,
      infoBox: true,
      sceneModePicker: false,
      selectionIndicator: true,
      timeline: false,
      navigationHelpButton: false,
      shouldAnimate: false,
      requestRenderMode: true,
      maximumRenderTimeChange: 0.05,
    });

    viewer.scene.globe.enableLighting = false;
    viewer.scene.globe.depthTestAgainstTerrain = false;
    viewer.scene.globe.maximumScreenSpaceError = 2; // Smooth LOD
    viewer.scene.postProcessStages.fxaa.enabled = true;

    // Initially show full globe view (altitude tuned so the whole globe fits
    // the shorter landscape panel without clipping)
    viewer.camera.setView({
      destination: Cesium.Cartesian3.fromDegrees(-51.9253, -14.235, 11500000),
    });

    viewerRef.current = viewer;

    return () => {
      if (viewerRef.current && !viewerRef.current.isDestroyed()) {
        viewerRef.current.destroy();
        viewerRef.current = null;
      }
    };
  }, []);

  // ── Route rendering ──────────────────────────────────────────────────
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    viewer.entities.removeAll();

    if (!origin || !destination) {
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(-51.9253, -14.235, 11500000),
        duration: 1.2,
      });
      viewer.scene.requestRender();
      return;
    }

    const originPos = Cesium.Cartesian3.fromDegrees(origin.lng, origin.lat, 10);
    const destPos = Cesium.Cartesian3.fromDegrees(destination.lng, destination.lat, 10);

    // ── 1. Origin Marker (Seller / Warehouse) ──
    viewer.entities.add({
      name: `Fulfillment Origin: ${origin.city ? origin.city.toUpperCase() + ", " : ""}${origin.state || ""}`,
      position: originPos,
      point: {
        pixelSize: 13,
        color: Cesium.Color.fromCssColorString(accentColor || "#b45309"),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 3,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      label: {
        text: `📦 ORIGIN\n${origin.city ? origin.city.toUpperCase() + ", " : ""}${origin.state || ""}`,
        font: "bold 12px sans-serif",
        fillColor: Cesium.Color.WHITE,
        backgroundColor: Cesium.Color.fromAlpha(Cesium.Color.BLACK, 0.8),
        showBackground: true,
        style: Cesium.LabelStyle.FILL,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -16),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      description: `
        <div style="font-family: sans-serif; padding: 6px;">
          <h4 style="margin:0 0 6px 0; color: #b45309;">Shipment Origin (Seller/Warehouse)</h4>
          <p><strong>Seller ID:</strong> ${origin.id || "N/A"}</p>
          <p><strong>Location:</strong> ${origin.city || "Unknown"}, ${origin.state || "N/A"}</p>
          <p><strong>Coordinates:</strong> ${origin.lat.toFixed(4)}, ${origin.lng.toFixed(4)}</p>
        </div>
      `,
    });

    // ── 2. Destination Marker (Customer) ──
    viewer.entities.add({
      name: `Delivery Destination: ${destination.city ? destination.city.toUpperCase() + ", " : ""}${destination.state || ""}`,
      position: destPos,
      point: {
        pixelSize: 13,
        color: Cesium.Color.fromCssColorString("#0f766e"),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 3,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      label: {
        text: `🏠 DESTINATION\n${destination.city ? destination.city.toUpperCase() + ", " : ""}${destination.state || ""}`,
        font: "bold 12px sans-serif",
        fillColor: Cesium.Color.WHITE,
        backgroundColor: Cesium.Color.fromAlpha(Cesium.Color.BLACK, 0.8),
        showBackground: true,
        style: Cesium.LabelStyle.FILL,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -16),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      description: `
        <div style="font-family: sans-serif; padding: 6px;">
          <h4 style="margin:0 0 6px 0; color: #0f766e;">Delivery Destination (Customer)</h4>
          <p><strong>Customer ID:</strong> ${destination.id || "N/A"}</p>
          <p><strong>Location:</strong> ${destination.city || "Unknown"}, ${destination.state || "N/A"}</p>
          <p><strong>Coordinates:</strong> ${destination.lat.toFixed(4)}, ${destination.lng.toFixed(4)}</p>
        </div>
      `,
    });

    // ── 3. Road Route (Google Maps navigation styling: white casing + solid blue line) ──
    let polylinePositions: Cesium.Cartesian3[] = [];
    if (geometry && geometry.length > 0) {
      const flatCoords: number[] = [];
      geometry.forEach(([lng, lat]) => {
        flatCoords.push(lng, lat, 15);
      });
      polylinePositions = Cesium.Cartesian3.fromDegreesArrayHeights(flatCoords);
    } else {
      polylinePositions = [originPos, destPos];
    }

    const routeDescription = `
      <div style="font-family: sans-serif; padding: 6px;">
        <h4 style="margin:0 0 6px 0; color: #1a73e8;">Road Route</h4>
        <p><strong>Road Distance:</strong> ${distanceKm ? distanceKm.toFixed(1) + " km" : "N/A"}</p>
        <p><strong>Estimated Drive Time:</strong> ${durationFormatted || "N/A"}</p>
      </div>
    `;

    // White casing underneath (the Google Maps outline effect)
    viewer.entities.add({
      name: `Calculated Route (${distanceKm ? distanceKm.toFixed(1) + " km" : "Road Network"})`,
      polyline: {
        positions: polylinePositions,
        width: 11,
        material: Cesium.Color.WHITE,
        clampToGround: false,
      },
      description: routeDescription,
    });
    // Solid Google-blue navigation line on top
    viewer.entities.add({
      polyline: {
        positions: polylinePositions,
        width: 6,
        material: Cesium.Color.fromCssColorString(GOOGLE_ROUTE_BLUE),
        clampToGround: false,
      },
      description: routeDescription,
    });

    // ── 4. Precise Camera Fly-To: Concise, Clean Google Maps Style Framing ──
    try {
      const allCartesians = [originPos, destPos, ...polylinePositions];
      const sphere = Cesium.BoundingSphere.fromPoints(allCartesians);
      viewer.camera.flyToBoundingSphere(sphere, {
        duration: 1.4,
        offset: new Cesium.HeadingPitchRange(
          Cesium.Math.toRadians(0),
          Cesium.Math.toRadians(-62),
          sphere.radius * 2.1,
        ),
      });
    } catch {
      viewer.flyTo(viewer.entities, { duration: 1.4 });
    }

    viewer.scene.requestRender();
  }, [origin, destination, geometry, distanceKm, durationFormatted, accentColor]);

  // ── Jump directly above a location (straight down, no motion along route) ──
  const jumpTo = (point: RoutePoint) => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed() || !point) return;
    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 8000),
      orientation: {
        heading: Cesium.Math.toRadians(0),
        pitch: Cesium.Math.toRadians(-90),
        roll: 0,
      },
      duration: 1.6,
    });
  };

  return (
    <div className="cesium-route-container">
      <div ref={containerRef} className="cesium-viewer-canvas" />
      <div className="cesium-route-badge">
        <span className="cesium-badge-dot" style={{ background: accentColor }} />
        <span>3D Cesium Road Route Intelligence</span>
        {distanceKm ? (
          <span className="cesium-badge-dist">
            • <strong>{distanceKm.toLocaleString()} km</strong> road route ({durationFormatted || ""})
          </span>
        ) : null}
      </div>
      {origin && destination && (
        <div className="cesium-jump-btns">
          <button className="cesium-jump-btn" onClick={() => jumpTo(origin)}>
            📦 Origin
          </button>
          <button className="cesium-jump-btn cesium-jump-btn-dest" onClick={() => jumpTo(destination)}>
            🏠 Destination
          </button>
        </div>
      )}
    </div>
  );
};
