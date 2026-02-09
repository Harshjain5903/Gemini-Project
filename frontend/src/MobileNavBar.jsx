import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import './MobileNavBar.css';

export default function MobileNavBar({ show, instruction, nextInstruction, progress, onTap }) {
  const formatDist = (m) => {
    if (!m) return '';
    if (m >= 1000) return `${(m / 1000).toFixed(1)} km`;
    return `${Math.round(m)} m`;
  };

  return (
    <AnimatePresence>
      {show && instruction && (
        <motion.div
          className="mobile-nav-bar"
          initial={{ y: -100, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -100, opacity: 0 }}
          transition={{ type: 'spring', damping: 25, stiffness: 300 }}
          onClick={onTap}
        >
          <div className="mnb-progress" style={{ width: `${progress || 0}%` }} />
          <div className="mnb-content">
            <span className="mnb-icon">{instruction.icon}</span>
            <div className="mnb-text">
              <span className="mnb-instruction">{instruction.label}</span>
              {nextInstruction && (
                <span className="mnb-next">
                  Then {nextInstruction.icon} {nextInstruction.label}
                  {nextInstruction.distance > 0 ? ` in ${formatDist(nextInstruction.distance)}` : ''}
                </span>
              )}
            </div>
            <svg className="mnb-chevron" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
