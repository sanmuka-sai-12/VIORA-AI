// Future feature module entrypoints and mock adapters
export interface FeatureRegistry {
  identity: { enabled: boolean };
  institutions: { enabled: boolean };
  sources: { enabled: boolean };
  tutor: { enabled: boolean };
  lessons: { enabled: boolean };
  assessments: { enabled: boolean };
  analytics: { enabled: boolean };
}

export const featureRegistry: FeatureRegistry = {
  identity: { enabled: false },
  institutions: { enabled: false },
  sources: { enabled: false },
  tutor: { enabled: false },
  lessons: { enabled: false },
  assessments: { enabled: false },
  analytics: { enabled: false },
};

