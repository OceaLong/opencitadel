"use client";

import {
  type Transition,
  type Variants,
  motion,
  useReducedMotion as useMotionReducedMotion,
} from "motion/react";

export { motion, AnimatePresence } from "motion/react";

export function usePrefersReducedMotion(): boolean {
  return useMotionReducedMotion() ?? false;
}

const instant: Transition = { duration: 0 };

export function motionTransition(reduced: boolean, transition: Transition): Transition {
  return reduced ? instant : transition;
}

export const fadeInUp: Variants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0 },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1 },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.92 },
  visible: { opacity: 1, scale: 1 },
};

export const bounceIn: Variants = {
  hidden: { opacity: 0, scale: 0.6, y: 20 },
  visible: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { type: "spring", stiffness: 420, damping: 18 },
  },
};

export const staggerContainer = (stagger = 0.08, delay = 0): Variants => ({
  hidden: {},
  visible: {
    transition: { staggerChildren: stagger, delayChildren: delay },
  },
});

export function reducedVariants(variants: Variants, reduced: boolean): Variants {
  if (!reduced) return variants;
  return {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { duration: 0.15 } },
  };
}
