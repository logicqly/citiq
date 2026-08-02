interface RunControlsProps {
  clientName: string;
  isRunning: boolean;
  onStart: () => void;
  error?: string | null;
}

export function RunControls({ clientName, isRunning, onStart, error }: RunControlsProps) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm text-gray-400 uppercase tracking-widest mb-1">Monitoring</p>
        <h1 className="text-2xl font-bold text-white">{clientName}</h1>
      </div>
      <div className="flex flex-col items-end gap-2">
        <button
          onClick={onStart}
          disabled={isRunning}
          className="px-5 py-2.5 rounded-lg font-semibold text-sm transition-all
            bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-700
            disabled:text-gray-400 disabled:cursor-not-allowed text-white"
        >
          {isRunning ? "Running..." : "Start New Run"}
        </button>
        {error && (
          <p className="text-xs text-red-400 max-w-xs text-right">{error}</p>
        )}
      </div>
    </div>
  );
}
