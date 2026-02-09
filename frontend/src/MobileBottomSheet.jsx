import React, { useState, useEffect, useMemo } from 'react';
import { motion, useDragControls } from 'framer-motion';
import './MobileBottomSheet.css';

export default function MobileBottomSheet({ position, onPositionChange, children }) {
  const [windowHeight, setWindowHeight] = useState(
    window.visualViewport?.height || window.innerHeight
  );
  const controls = useDragControls();

  useEffect(() => {
    const update = () =>
      setWindowHeight(window.visualViewport?.height || window.innerHeight);
    window.addEventListener('resize', update);
    window.visualViewport?.addEventListener('resize', update);
    return () => {
      window.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('resize', update);
    };
  }, []);

  const snapPoints = useMemo(
    () => ({
      collapsed: windowHeight - 60,
      peek: windowHeight * 0.55,
      expanded: windowHeight * 0.08,
    }),
    [windowHeight]
  );

  const handleDragEnd = (_event, info) => {
    const currentY = snapPoints[position] + info.offset.y;
    const vy = info.velocity.y;

    const ordered = ['expanded', 'peek', 'collapsed'];
    const idx = ordered.indexOf(position);

    // Fast flick → jump to next snap point
    if (vy > 500) {
      onPositionChange(ordered[Math.min(idx + 1, 2)]);
      return;
    }
    if (vy < -500) {
      onPositionChange(ordered[Math.max(idx - 1, 0)]);
      return;
    }

    // Otherwise snap to nearest
    let closest = 'collapsed';
    let closestDist = Infinity;
    for (const key of ordered) {
      const d = Math.abs(currentY - snapPoints[key]);
      if (d < closestDist) {
        closestDist = d;
        closest = key;
      }
    }
    onPositionChange(closest);
  };

  return (
    <motion.div
      className="mobile-bottom-sheet"
      drag="y"
      dragControls={controls}
      dragListener={false}
      dragConstraints={{
        top: snapPoints.expanded,
        bottom: snapPoints.collapsed,
      }}
      dragElastic={0.1}
      onDragEnd={handleDragEnd}
      animate={{ y: snapPoints[position] }}
      transition={{ type: 'spring', damping: 30, stiffness: 300 }}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        height: windowHeight,
        zIndex: 20,
        pointerEvents: 'none',
      }}
    >
      <div className="sheet-surface">
        <div
          className="sheet-handle"
          onPointerDown={(e) => controls.start(e)}
        >
          <div className="sheet-handle-bar" />
        </div>
        <div className="sheet-content">{children}</div>
      </div>
    </motion.div>
  );
}
