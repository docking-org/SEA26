import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import Layout from './components/Layout';
import 'altcha';

// Mirrors Python sanitize() in generate_boltz_jobs.py
function sanitize(name) {
    return String(name).replace(/[^a-zA-Z0-9\-_.]/g, '_').replace(/^_+|_+$/g, '');
}

// Custom hook for responsive breakpoints
function useWindowSize() {
    const [windowSize, setWindowSize] = useState({
        width: typeof window !== 'undefined' ? window.innerWidth : 1200,
        height: typeof window !== 'undefined' ? window.innerHeight : 800,
    });

    useEffect(() => {
        function handleResize() {
            setWindowSize({
                width: window.innerWidth,
                height: window.innerHeight,
            });
        }

        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    return windowSize;
}

// Molecule SVG component using RDKit.js
function MoleculeSVG({ smiles, width = 200, height = 150 }) {
    const canvasRef = useRef(null);
    const [error, setError] = useState(false);

    useEffect(() => {
        if (!smiles || !window.RDKit) {
            setError(true);
            return;
        }

        try {
            const mol = window.RDKit.get_mol(smiles);
            if (mol) {
                const svg = mol.get_svg(width, height);
                if (canvasRef.current) {
                    canvasRef.current.innerHTML = svg;
                }
                mol.delete();
                setError(false);
            } else {
                setError(true);
            }
        } catch (e) {
            console.error("RDKit error:", e);
            setError(true);
        }
    }, [smiles, width, height]);

    if (error) {
        return (
            <div style={{ 
                width, 
                height, 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                backgroundColor: '#f5f5f5',
                border: '1px solid #ddd',
                borderRadius: '4px',
                color: '#999',
                fontSize: '12px'
            }}>
                Unable to render molecule
            </div>
        );
    }

    return <div ref={canvasRef} style={{ width, height }} />;
}

export default function CompoundVsTarget() {
    const [searchParams] = useSearchParams();
    const taskId = searchParams.get("taskId") || '';
    const [allCompounds, setAllCompounds] = useState([]);
    const [selectedCompoundIndex, setSelectedCompoundIndex] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [rdkitReady, setRdkitReady] = useState(false);
    const [moleculeImages, setMoleculeImages] = useState({});
    const [tooltip, setTooltip] = useState({
        visible: false,
        x: 0,
        y: 0,
        target: '',
        hitId: '',
        hitSmiles: '',
        score: 0
    });
    const [lockedTooltip, setLockedTooltip] = useState(null); // NEW: locked/pinned state
    const [focusedCell, setFocusedCell] = useState({ row: -1, col: -1 });
    const [boltzStatus, setBoltzStatus] = useState(null); // null | 'loading' | 'success' | 'error'
    const [boltzMessage, setBoltzMessage] = useState('');
    const [boltzJobsByChembl, setBoltzJobsByChembl] = useState({}); // chemblId -> [job names]
    const [boltzJobs, setBoltzJobs] = useState({}); // jobKey -> { task_id, status }
    const [heatmapSubmitting, setHeatmapSubmitting] = useState({}); // jobKey -> bool
    const [selectedBoltzJob, setSelectedBoltzJob] = useState(''); // full job name e.g. "CHEMBL123__LigandName"
    const altchaRef = useRef(null);
    const altchaHandlerRef = useRef(null);
    const [altchaPayload, setAltchaPayload] = useState(null);
    const [altchaVerified, setAltchaVerified] = useState(false);
    const rightColumnRef = useRef(null);
    const heatmapRef = useRef(null);
    const [rightColumnWidth, setRightColumnWidth] = useState(0);
    const { width: windowWidth, height: windowHeight } = useWindowSize();
    
    // Responsive breakpoints
    const isMobile = windowWidth < 640;
    const isTablet = windowWidth >= 640 && windowWidth < 1024;
    const isDesktop = windowWidth >= 1024;

    // Load RDKit.js
    useEffect(() => {
        const loadRDKit = async () => {
            try {
                if (!window.RDKit) {
                    await new Promise((resolve, reject) => {
                        const script = document.createElement('script');
                        script.src = 'https://unpkg.com/@rdkit/rdkit/dist/RDKit_minimal.js';
                        script.onload = resolve;
                        script.onerror = reject;
                        document.head.appendChild(script);
                    });
                    
                    window.initRDKitModule().then((RDKit) => {
                        window.RDKit = RDKit;
                        setRdkitReady(true);
                        console.log('RDKit.js loaded successfully');
                    });
                } else {
                    setRdkitReady(true);
                }
            } catch (error) {
                console.error('Error loading RDKit.js:', error);
            }
        };
        
        loadRDKit();
    }, []);

    useEffect(() => {
        const el = rightColumnRef.current;
        if (!el) return;
        const ro = new ResizeObserver(entries => {
            setRightColumnWidth(entries[0].contentRect.width);
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    function generateMoleculeSVG(smiles, id, width = 200, height = 200) {
        try {
            if (!window.RDKit || !rdkitReady) {
                console.log('RDKit not ready yet');
                return null;
            }

            const mol = window.RDKit.get_mol(smiles);
            if (!mol || !mol.is_valid()) {
                console.error('Invalid molecule:', smiles);
                return null;
            }

            const svg = mol.get_svg(width, height);
            mol.delete();
            
            setMoleculeImages(prev => ({
                ...prev,
                [id]: svg
            }));
            
            return svg;
        } catch (error) {
            console.error('Error generating SVG for', smiles, error);
            return null;
        }
    }

    // Get the currently selected compound's data
    const heatmapData = useMemo(() => {
        if (allCompounds.length === 0) return null;
        return allCompounds[selectedCompoundIndex];
    }, [allCompounds, selectedCompoundIndex]);

    async function fetchHeatMapData() {
        try {
            setLoading(true);
            const response = await fetch(`/api/compound_vs_target?task_id=${taskId}`);
            if (response.ok) {
                const data = await response.json();
                setAllCompounds(data);
                setSelectedCompoundIndex(0);
            } else {
                setError("Failed to fetch heatmap data");
                console.error("Failed to fetch heatmap data");
            }
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }

    const tableData = useMemo(() => {
        if (!heatmapData || !heatmapData.hits) return null;

        const targets = Object.keys(heatmapData.hits);
        
        const hitsPerTarget = {};
        targets.forEach(target => {
            hitsPerTarget[target] = heatmapData.hits[target].map(hit => ({
                id: hit.Known_Hit_ID,
                score: hit.Tanimoto_Score,
                smiles: hit.Known_Hit_SMILES
            }));
        });

        const maxHits = Math.max(...targets.map(t => hitsPerTarget[t].length));

        return { targets, hitsPerTarget, maxHits };
    }, [heatmapData]);

    const STATUS_RGB = {
        queued:  [107, 114, 128],
        pending: [217, 119, 6],
        running: [37, 99, 235],
        done: [22, 163, 74],
        failed: [220, 38, 38],
    };

    const getColorForScore = (score, jobStatus) => {
        if (score === undefined) return '#ffffff';
        const intensity = Math.min(score, 1);
        if (jobStatus && STATUS_RGB[jobStatus]) {
            const [r, g, b] = STATUS_RGB[jobStatus];
            return `rgb(${Math.round(r * intensity + 255 * (1 - intensity))}, ${Math.round(g * intensity + 255 * (1 - intensity))}, ${Math.round(b * intensity + 255 * (1 - intensity))})`;
        }
        const gray = Math.round(255 * (1 - intensity));
        return `rgb(${gray}, ${gray}, ${gray})`;
    };

    const getTextColor = (score) => {
        if (score === undefined) return '#000000';
        const intensity = Math.min(score * 5, 1);
        return intensity > 0.5 ? '#ffffff' : '#000000';
    };

    const handleMouseEnter = (e, target, hit) => {
        if (!hit || lockedTooltip) return; // Don't hover if locked
        const rect = e.target.getBoundingClientRect();
        setTooltip({
            visible: true,
            x: rect.right + 10,
            y: rect.top,
            target,
            hitId: hit.id,
            hitSmiles: hit.smiles,
            score: hit.score
        });
    };

    const handleMouseLeave = () => {
        if (!lockedTooltip) { // Only hide if not locked
            setTooltip(prev => ({ ...prev, visible: false }));
        }
    };

    // Click to lock/unlock tooltip
    const handleCellClick = (e, target, hit) => {
        if (!hit) return;
        
        // If clicking the same cell that's already locked, unlock it
        if (lockedTooltip && lockedTooltip.hitId === hit.id && lockedTooltip.target === target) {
            setLockedTooltip(null);
            setTooltip(prev => ({ ...prev, visible: false }));
        } else {
            // Lock to the new cell
            const newTooltip = {
                visible: true,
                x: 0,
                y: 0,
                target,
                hitId: hit.id,
                hitSmiles: hit.smiles,
                score: hit.score
            };
            setTooltip(newTooltip);
            setLockedTooltip(newTooltip);
        }
    };

    // Keyboard arrow navigation for heatmap
    const handleKeyDown = (e) => {
        if (!tableData) return;
        const maxRow = tableData.targets.length - 1;
        const maxCol = tableData.maxHits - 1;

        let newRow = focusedCell.row;
        let newCol = focusedCell.col;

        switch (e.key) {
            case 'ArrowUp':
                e.preventDefault();
                newRow = focusedCell.row <= 0 ? 0 : focusedCell.row - 1;
                break;
            case 'ArrowDown':
                e.preventDefault();
                newRow = focusedCell.row >= maxRow ? maxRow : focusedCell.row + 1;
                break;
            case 'ArrowLeft':
                e.preventDefault();
                newCol = focusedCell.col <= 0 ? 0 : focusedCell.col - 1;
                break;
            case 'ArrowRight':
                e.preventDefault();
                newCol = focusedCell.col >= maxCol ? maxCol : focusedCell.col + 1;
                break;
            case 'Enter':
            case ' ':
                e.preventDefault();
                if (focusedCell.row >= 0 && focusedCell.col >= 0) {
                    const target = tableData.targets[focusedCell.row];
                    const hit = tableData.hitsPerTarget[target][focusedCell.col];
                    if (hit) handleCellClick(e, target, hit);
                }
                return;
            default:
                return;
        }

        // Initialize to 0,0 if not yet focused
        if (focusedCell.row < 0) newRow = 0;
        if (focusedCell.col < 0) newCol = 0;

        setFocusedCell({ row: newRow, col: newCol });

        // Show tooltip for the focused cell
        const target = tableData.targets[newRow];
        const hit = tableData.hitsPerTarget[target][newCol];
        if (hit && !lockedTooltip) {
            setTooltip({
                visible: true,
                x: 0,
                y: 0,
                target,
                hitId: hit.id,
                hitSmiles: hit.smiles,
                score: hit.score
            });
        } else if (!hit && !lockedTooltip) {
            setTooltip(prev => ({ ...prev, visible: false }));
        }
    };

    // Clear lock when compound changes
    const handleCompoundChange = (e) => {
        setSelectedCompoundIndex(Number(e.target.value));
        setLockedTooltip(null);
        setTooltip(prev => ({ ...prev, visible: false }));
    };

    async function submitBoltz() {
        setBoltzStatus('loading');
        setBoltzMessage('');
        try {
            const resp = await fetch('/api/generate_boltz_jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId }),
            });
            const data = await resp.json();
            if (resp.ok) {
                setBoltzStatus('success');
                setBoltzMessage(`${data.n_jobs} Boltz job${data.n_jobs !== 1 ? 's' : ''} generated`);
                fetchBoltzStatus();
            } else {
                setBoltzStatus('error');
                setBoltzMessage(data.detail || 'Failed to generate Boltz jobs');
            }
        } catch (err) {
            setBoltzStatus('error');
            setBoltzMessage(err.message);
        }
    }

    function fetchBoltzStatus() {
        fetch(`/api/boltz_status?task_id=${taskId}`)
            .then(r => r.json())
            .then(data => setBoltzJobsByChembl(data.jobs_by_chembl || {}))
            .catch(() => {});
    }

    function fetchJobStatus() {
        fetch(`/api/boltz_job_status?task_id=${taskId}`)
            .then(r => r.json())
            .then(data => setBoltzJobs(data || {}))
            .catch(() => {});
    }

    async function submitBoltzFromHeatmap(chemblId, ligandName, smiles) {
        const jobKey = `${sanitize(chemblId)}__${sanitize(ligandName)}`;
        setHeatmapSubmitting(prev => ({ ...prev, [jobKey]: true }));
        try {
            const resp = await fetch('/api/boltz_submit_from_heatmap', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_id: taskId,
                    chembl_id: chemblId,
                    smiles,
                    ligand_name: ligandName,
                    altcha: altchaPayload,
                }),
            });
            const data = await resp.json();
            if (resp.ok) {
                if (data.status === 'already_done') {
                    setBoltzJobs(prev => ({
                        ...prev,
                        [data.job_key]: { ...prev[data.job_key], status: 'done', chembl_id: chemblId, ligand: ligandName },
                    }));
                } else if (data.status === 'already_running') {
                    fetchJobStatus();
                } else {
                    setBoltzJobs(prev => ({
                        ...prev,
                        [data.job_key]: { task_id: null, chembl_id: chemblId, ligand: ligandName, status: 'queued' },
                    }));
                }
            } else {
                alert(`Boltz2 submission failed: ${data.detail || 'unknown error'}`);
            }
        } catch (err) {
            alert(`Boltz2 submission error: ${err.message}`);
        } finally {
            setHeatmapSubmitting(prev => ({ ...prev, [jobKey]: false }));
        }
    }

    async function submitBothFromHeatmap(chemblId, queryName, querySmiles, hitId, hitSmiles) {
        const queryJobKey = `${sanitize(chemblId)}__${sanitize(queryName)}`;
        const hitJobKey   = hitId ? `${sanitize(chemblId)}__${sanitize(hitId)}` : null;
        const queryDone   = boltzJobs[queryJobKey]?.status === 'done';
        const hitDone     = !hitSmiles || boltzJobs[hitJobKey]?.status === 'done';
        const queryActive = ['queued', 'pending', 'running'].includes(boltzJobs[queryJobKey]?.status);
        const hitActive   = hitSmiles && ['queued', 'pending', 'running'].includes(boltzJobs[hitJobKey]?.status);
        const promises = [];
        if (!queryDone && !queryActive) promises.push(submitBoltzFromHeatmap(chemblId, queryName, querySmiles));
        if (hitSmiles && !hitDone && !hitActive) promises.push(submitBoltzFromHeatmap(chemblId, hitId, hitSmiles));
        await Promise.all(promises);
    }

    useEffect(() => {
        if (taskId) {
            fetchHeatMapData();
            fetchBoltzStatus();
            fetchJobStatus();
        }
    }, [taskId]);

    const [altchaDisabled, setAltchaDisabled] = useState(null);
    const [isInternal, setIsInternal] = useState(false);

    useEffect(() => {
        fetch('/api/is_internal')
            .then(r => r.json())
            .then(data => { console.log('client IP:', data.ip); setIsInternal(!!data.internal); })
            .catch(() => setIsInternal(false));
    }, []);

    useEffect(() => {
        fetch('/api/captcha/challenge')
            .then(r => r.json())
            .then(data => {
                if (data.disabled) {
                    setAltchaDisabled(true);
                    setAltchaVerified(true);
                } else {
                    setAltchaDisabled(false);
                }
            })
            .catch(() => { setAltchaDisabled(false); });
    }, []);

    // Callback ref — runs when the widget actually mounts/unmounts (conditional render safe)
    const altchaCallbackRef = useCallback((widget) => {
        if (altchaRef.current && altchaHandlerRef.current) {
            altchaRef.current.removeEventListener('statechange', altchaHandlerRef.current);
        }
        altchaRef.current = widget;
        if (!widget) return;
        const handleStateChange = (ev) => {
            const { state, payload } = ev.detail;
            if (state === 'verified' && payload) {
                setAltchaPayload(payload);
                setAltchaVerified(true);
            } else if (state === 'unverified' || state === 'error') {
                setAltchaPayload(null);
                setAltchaVerified(false);
            }
            // ignore 'verifying' — transient state, don't drop existing verified status
        };
        altchaHandlerRef.current = handleStateChange;
        widget.addEventListener('statechange', handleStateChange);
    }, []);

    function handleJobRowHover(chemblId, ligandId) {
        if (!tableData) return;
        const rowIndex = tableData.targets.findIndex(t => t.split(':')[0] === chemblId);
        if (rowIndex === -1) return;
        const colIndex = tableData.hitsPerTarget[tableData.targets[rowIndex]].findIndex(h => h.id === ligandId);
        if (colIndex === -1) return;
        setFocusedCell({ row: rowIndex, col: colIndex });
    }

    function handleJobRowLeave() {
        setFocusedCell({ row: -1, col: -1 });
    }

    // Poll job status every 30 s while any job is active
    useEffect(() => {
        const hasActive = Object.values(boltzJobs).some(
            j => j.status === 'queued' || j.status === 'running'
        );
        if (!hasActive) return;
        const id = setInterval(() => {
            fetch(`/api/boltz_job_status?task_id=${taskId}`)
                .then(r => r.json())
                .then(data => setBoltzJobs(data || {}))
                .catch(() => {});
        }, 30000);
        return () => clearInterval(id);
    }, [boltzJobs, taskId]);

    useEffect(() => {
        console.log("heatmapData updated:", heatmapData);
    }, [heatmapData]);


    // Estimate the width of the longest target label
    const targetLabelWidth = useMemo(() => {
        if (!tableData) return 100;
        const longestLabel = Math.max(...tableData.targets.map(t => t.split(':')[0].length));
        // Approximate character width at 12px font size
        return longestLabel * 8 + 20; // 8px per char + padding/margin
    }, [tableData]);

    // Calculate dynamic cell size based on screen size, number of hits, and available height
    const cellSize = useMemo(() => {
        if (!tableData) return { width: 30, height: 25 };

        // Available width = half the window (for the heatmap side) minus target labels, gap, and padding
        let availableWidth;
        if (isMobile) {
            availableWidth = windowWidth - targetLabelWidth - 40;
        } else if (isTablet) {
            availableWidth = windowWidth * 0.5 - targetLabelWidth - 40;
        } else {
            availableWidth = windowWidth * 0.5 - targetLabelWidth - 60;
        }

        const calculatedWidth = Math.min(
            60,
            Math.max(
                isMobile ? 14 : 18,
                Math.floor(availableWidth / tableData.maxHits)
            )
        );

        // Calculate height so all rows fit within the viewport
        const availableHeight = windowHeight - (isMobile ? 250 : 350);
        const calculatedHeight = Math.min(
            50,
            Math.max(
                isMobile ? 14 : 18,
                Math.floor(availableHeight / tableData.targets.length)
            )
        );

        return {
            width: calculatedWidth,
            height: calculatedHeight
        };
    }, [tableData, isMobile, isTablet, windowWidth, windowHeight, targetLabelWidth]);

    // Responsive molecule image sizes — derived from the actual right column width
    const moleculeSize = useMemo(() => {
        if (isMobile) return { width: 150, height: 120 };
        if (isTablet) return { width: 180, height: 150 };
        // Card padding (40px) + gap between two molecules (20px) = 60px overhead
        const available = rightColumnWidth > 0 ? rightColumnWidth : windowWidth * 0.4;
        const w = Math.min(Math.max(160, Math.floor((available - 60) / 2)), 380);
        return { width: w, height: Math.floor(w * 0.82) };
    }, [isMobile, isTablet, rightColumnWidth, windowWidth]);

    // Determine which tooltip data to display (locked takes priority)
    const displayTooltip = lockedTooltip || tooltip;
    const showTooltip = lockedTooltip ? true : tooltip.visible;

    return (
        <Layout>
              <Link 
                        to={`/result?taskId=${taskId}`}
                        className=" hover:underline mb-4 inline-block"
                    >
                        ← Back to result
                    </Link>
            <h1 className={`font-bold ${isMobile ? 'text-xl' : 'text-2xl'} mb-2`} >
                Compound vs Target Heatmap
            </h1>
            <p className={`mb-2 ${isMobile ? 'text-sm' : ''}`}>Task ID: {taskId}</p>
            
            {/* Compound Selector Dropdown */}
            {allCompounds.length > 0 && (
                <div style={{ 
                    marginBottom: '20px',
                    display: 'flex',
                    flexDirection: isMobile ? 'column' : 'row',
                    alignItems: isMobile ? 'flex-start' : 'center',
                    gap: isMobile ? '10px' : '0'
                }}>
                    <label htmlFor="compound-select" style={{ 
                        marginRight: isMobile ? '0' : '10px', 
                        fontWeight: 'bold',
                        fontSize: isMobile ? '14px' : '16px'
                    }}>
                        Select Compound:
                    </label>
                    <select 
                        id="compound-select"
                        value={selectedCompoundIndex}
                        onChange={handleCompoundChange}
                        style={{
                            padding: isMobile ? '6px 10px' : '8px 12px',
                            fontSize: isMobile ? '13px' : '14px',
                            borderRadius: '4px',
                            border: '1px solid #ccc',
                            minWidth: isMobile ? '100%' : '250px',
                            maxWidth: isMobile ? '100%' : 'none',
                            cursor: 'pointer'
                        }}
                    >
                        {allCompounds.map((compound, index) => (
                            <option key={index} value={index}>
                                {compound.Name} ({Object.keys(compound.hits || {}).length} targets)
                            </option>
                        ))}
                    </select>
                    <span style={{ 
                        marginLeft: isMobile ? '0' : '15px', 
                        color: '#fff', 
                        fontSize: isMobile ? '12px' : '13px' 
                    }}>
                        {allCompounds.length} compound{allCompounds.length !== 1 ? 's' : ''} available
                    </span>
                </div>
            )}

            {heatmapData && (
                <p style={{
                    fontSize: isMobile ? '12px' : '14px',
                    wordBreak: 'break-all',
                    marginBottom: '8px'
                }}>
                    <strong>Compound:</strong> {heatmapData.Name}
                    {!isMobile && <> | <strong>SMILES:</strong> {heatmapData.SMILES}</>}
                </p>
            )}

{loading && <p>Loading...</p>}
            {error && <p style={{ color: 'red' }}>{error}</p>}
            {!rdkitReady && <p style={{ color: '#666' }}>Loading RDKit...</p>}
            
    
            {/* Main content area - stacked on mobile, side by side on desktop */}
            <div style={{
                display: 'flex',
                flexDirection: isMobile ? 'column' : 'row',
                gap: '20px',
                marginTop: '20px',
                alignItems: isMobile ? 'flex-start' : 'stretch',
                width: '100%'
            }}>
                {/* Heatmap table */}
                <div style={{
                    flex: '1 1 0%',
                    minWidth: 0,
                    maxWidth: isMobile ? '100%' : '50%',
                    height: '100%',
                    overflow: 'auto',
                }}>
                    {tableData && (
                        <div style={{
                            display: 'flex'
                        }}>
                            <table
                                ref={heatmapRef}
                                tabIndex={0}
                                onKeyDown={handleKeyDown}
                                style={{
                                    borderCollapse: 'collapse',
                                    fontSize: isMobile ? '10px' : '11px',
                                    outline: 'none'
                                }}
                            >
                                <tbody>
                                    {tableData.targets.map((target, rowIndex) => (
                                        <tr key={target}>
                                            {Array.from({ length: tableData.maxHits }).map((_, colIndex) => {
                                                const hit = tableData.hitsPerTarget[target][colIndex];
                                                const isLocked = lockedTooltip &&
                                                    lockedTooltip.hitId === hit?.id &&
                                                    lockedTooltip.target === target;
                                                const isFocused = focusedCell.row === rowIndex && focusedCell.col === colIndex;
                                                const hitJobKey = hit ? `${sanitize(target.split(':')[0])}__${sanitize(hit.id)}` : null;
                                                const hitJobStatus = isInternal && hitJobKey ? boltzJobs[hitJobKey]?.status : null;
                                                return (
                                                    <td
                                                        key={colIndex}
                                                        onMouseEnter={isDesktop ? (e) => handleMouseEnter(e, target, hit) : undefined}
                                                        onMouseLeave={isDesktop ? handleMouseLeave : undefined}
                                                        onClick={(e) => handleCellClick(e, target, hit)}
                                                        style={{
                                                            backgroundColor: getColorForScore(hit?.score, hitJobStatus),
                                                            minWidth: `${cellSize.width}px`,
                                                            width: `${cellSize.width}px`,
                                                            height: `${cellSize.height}px`,
                                                            padding: 0,
                                                            cursor: hit ? 'pointer' : 'default',
                                                            border: isFocused
                                                                ? '2px solid #ff6600'
                                                                : isLocked
                                                                    ? '2px solid #007bff'
                                                                    : '1px solid #eee',
                                                            boxSizing: 'border-box'
                                                        }}
                                                    />
                                                );
                                            })}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            <div style={{
                                marginLeft: '10px',
                                fontSize: isMobile ? '10px' : '12px',
                                flexShrink: 0
                            }}>
                                {tableData.targets.map(target => (
                                    <div key={target} style={{
                                        height: `${cellSize.height}px`,
                                        lineHeight: `${cellSize.height}px`,
                                        whiteSpace: 'nowrap',
                                        borderBottom: '1px solid transparent'
                                    }}>
                                        {target.split(':')[1] || target.split(':')[0]}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Right column: molecule viewer + Boltz card */}
                <div ref={rightColumnRef} style={{
                    flex: '1 1 0%',
                    minWidth: 0,
                    width: '100%',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '20px',
                }}>
                    {/* Tooltip with Molecule Images */}
                    <div style={{
                        position: 'sticky',
                        top: '20px',
                    }}>
                    <div style={{
                        backgroundColor: 'white',
                        border: lockedTooltip ? '2px solid #007bff' : '1px solid #e0e0e0',
                        color: '#333',
                        borderRadius: '8px',
                        padding: isMobile ? '15px 15px' : '20px 20px',
                        boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                        width: '100%',
                        boxSizing: 'border-box',
                    }}>
                        {/* Locked indicator */}
                        {lockedTooltip && (
                            <div style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                marginBottom: '10px',
                                paddingBottom: '10px',
                                borderBottom: '1px solid #eee'
                            }}>
                                <span style={{
                                    fontSize: '12px',
                                    color: '#007bff',
                                    fontWeight: 'bold'
                                }}>
                                    Click the same cell or "Unlock" to release
                                </span>
                                <button
                                    onClick={() => {
                                        setLockedTooltip(null);
                                        setTooltip(prev => ({ ...prev, visible: false }));
                                    }}
                                    style={{
                                        padding: '4px 10px',
                                        backgroundColor: '#f8f9fa',
                                        border: '1px solid #ccc',
                                        borderRadius: '4px',
                                        cursor: 'pointer',
                                        fontSize: '12px'
                                    }}
                                >
                                    Unlock
                                </button>
                            </div>
                        )}

                        {/* Target details - shown on hover/click */}
                        {showTooltip && rdkitReady && (
                            <div style={{
                                marginBottom: '15px',
                                fontSize: isMobile ? '13px' : '14px',
                                paddingBottom: '15px',
                                borderBottom: '1px solid #eee'
                            }}>
                                <strong>Target ChEMBL ID:</strong> {displayTooltip.target.split(':')[0]}<br />
                                <strong>Target UniProt ID:</strong> {displayTooltip.target.split(':')[1] || 'N/A'}<br />
                                <strong>Target Name:</strong> {displayTooltip.target.split(':')[2]}<br />
                                <strong>Known Hit ID:</strong> {displayTooltip.hitId}<br />
                                <strong>Tanimoto Score:</strong> {displayTooltip.score.toFixed(4)}
                            </div>
                        )}

                        {/* Molecules side by side */}
                        <div style={{
                            display: 'flex',
                            flexDirection: isMobile ? 'column' : 'row',
                            gap: isMobile ? '15px' : '20px',
                            justifyContent: 'center',
                            alignItems: 'flex-start'
                        }}>
                            {/* Query Compound - always visible */}
                            {heatmapData && rdkitReady && (
                                <div style={{
                                    flex: 1,
                                    textAlign: 'center',
                                    maxWidth: isMobile ? '100%' : `${moleculeSize.width + 30}px`,
                                    width: '100%'
                                }}>
                                    <div style={{
                                        fontWeight: 'bold',
                                        marginBottom: '8px',
                                        fontSize: isMobile ? '12px' : '13px'
                                    }}>
                                        Query Compound
                                    </div>
                                    <div style={{
                                        fontSize: isMobile ? '10px' : '11px',
                                        color: '#666',
                                        marginBottom: '8px'
                                    }}>
                                        {heatmapData.Name}
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'center' }}>
                                        <MoleculeSVG
                                            smiles={heatmapData.SMILES}
                                            width={moleculeSize.width}
                                            height={moleculeSize.height}
                                        />
                                    </div>
                                    <div style={{
                                        fontSize: isMobile ? '9px' : '10px',
                                        color: '#888',
                                        marginTop: '8px',
                                        wordBreak: 'break-all',
                                        maxHeight: isMobile ? '40px' : 'none',
                                        overflow: 'hidden'
                                    }}>
                                        {heatmapData.SMILES}
                                    </div>
                                </div>
                            )}

                            {/* Known Hit - shown on hover/click */}
                            {showTooltip && rdkitReady ? (
                                <div style={{
                                    flex: 1,
                                    textAlign: 'center',
                                    maxWidth: isMobile ? '100%' : `${moleculeSize.width + 30}px`,
                                    width: '100%'
                                }}>
                                    <div style={{
                                        fontWeight: 'bold',
                                        marginBottom: '8px',
                                        fontSize: isMobile ? '12px' : '13px'
                                    }}>
                                        Known Hit
                                    </div>
                                    <div style={{
                                        fontSize: isMobile ? '10px' : '11px',
                                        color: '#666',
                                        marginBottom: '8px'
                                    }}>
                                        {displayTooltip.hitId}
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'center' }}>
                                        <MoleculeSVG
                                            smiles={displayTooltip.hitSmiles}
                                            width={moleculeSize.width}
                                            height={moleculeSize.height}
                                        />
                                    </div>
                                    <div style={{
                                        fontSize: isMobile ? '9px' : '10px',
                                        color: '#888',
                                        marginTop: '8px',
                                        wordBreak: 'break-all',
                                        maxHeight: isMobile ? '40px' : 'none',
                                        overflow: 'hidden'
                                    }}>
                                        {displayTooltip.hitSmiles}
                                    </div>
                                </div>
                            ) : (
                                <div style={{
                                    flex: 1,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    color: '#999',
                                    fontSize: isMobile ? '13px' : '14px',
                                    minHeight: isMobile ? '100px' : '180px'
                                }}>
                                    {isDesktop
                                        ? 'Hover to preview, click to pin. Use arrow keys to navigate, Enter/Space to pin.'
                                        : 'Tap a cell to see hit details'
                                    }
                                </div>
                            )}
                        </div>{/* end molecules side-by-side */}

                        {/* Submit to Boltz2 — only for internal users when cell is locked */}
                        {isInternal && lockedTooltip && heatmapData && (() => {
                            const chemblId   = lockedTooltip.target.split(':')[0];
                            const uniprotId = lockedTooltip.target.split(':')[1];
                            const targetName = lockedTooltip.target.split(':')[2] || chemblId;

                            const queryName   = heatmapData.Name;
                            const querySmiles = heatmapData.SMILES;
                            const hitId       = lockedTooltip.hitId;
                            const hitSmiles   = lockedTooltip.hitSmiles;
                            const hasHit      = !!(hitSmiles && lockedTooltip.score < 1);

                            const queryJobKey = `${sanitize(chemblId)}__${sanitize(queryName)}`;
                            const hitJobKey   = hasHit ? `${sanitize(chemblId)}__${sanitize(hitId)}` : null;

                            const queryJob    = boltzJobs[queryJobKey];
                            const hitJob      = hitJobKey ? boltzJobs[hitJobKey] : null;
                            const queryStatus = queryJob?.status;
                            const hitStatus   = hitJob?.status;
                            const queryDone   = queryStatus === 'done';
                            const hitDone     = !hasHit || hitStatus === 'done';
                            const bothDone    = queryDone && hitDone;

                            const queryActive = ['queued', 'pending', 'running'].includes(queryStatus);
                            const hitActive   = hasHit && ['queued', 'pending', 'running'].includes(hitStatus);

                            const anySubmitting = !!(heatmapSubmitting[queryJobKey] || (hitJobKey && heatmapSubmitting[hitJobKey]));

                            const needsQuery = !queryDone && !queryActive;
                            const needsHit   = hasHit && !hitDone && !hitActive;

                            let submitBtnLabel = null;
                            if (anySubmitting) {
                                submitBtnLabel = 'Submitting…';
                            } else if (needsQuery && needsHit) {
                                submitBtnLabel = `Submit Both × ${targetName}`;
                            } else if (needsQuery) {
                                submitBtnLabel = `Submit Query × ${targetName}`;
                            } else if (needsHit) {
                                submitBtnLabel = `Submit Hit × ${targetName}`;
                            }

                            const submitBtnDisabled = anySubmitting || queryActive || hitActive || !altchaVerified;

                            const statusLines = [];
                            if (queryStatus && !queryDone) {
                                const lbl = queryStatus === 'queued' ? 'Queued'
                                    : queryStatus === 'pending' ? `Pending #${queryJob?.task_id}`
                                    : queryStatus === 'running' ? `Running #${queryJob?.task_id}`
                                    : queryStatus === 'failed'  ? 'Failed'
                                    : queryStatus;
                                statusLines.push(`Query: ${lbl}`);
                            }
                            if (hasHit && hitStatus && !hitDone) {
                                const lbl = hitStatus === 'queued' ? 'Queued'
                                    : hitStatus === 'pending' ? `Pending #${hitJob?.task_id}`
                                    : hitStatus === 'running' ? `Running #${hitJob?.task_id}`
                                    : hitStatus === 'failed'  ? 'Failed'
                                    : hitStatus;
                                statusLines.push(`Hit: ${lbl}`);
                            }

                            const ligandsParam = hasHit
                                ? encodeURIComponent(`${queryName},${hitId}`)
                                : encodeURIComponent(queryName);
                            const downloadBothUrl = `/api/boltz_download?task_id=${taskId}&chembl_id=${chemblId}&ligands=${ligandsParam}`;
                            const chimeraxUrl     = `${downloadBothUrl}&include_chimerax_script=true`;

                            const retryBtnStyle = (disabled) => ({
                                width: '100%', padding: '6px 14px',
                                backgroundColor: disabled ? '#e5e7eb' : '#dc2626',
                                color: disabled ? '#9ca3af' : 'white',
                                border: 'none', borderRadius: '6px',
                                cursor: disabled ? 'not-allowed' : 'pointer',
                                fontWeight: '600', fontSize: '12px',
                            });

                            return (
                                <div style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid #eee' }}>
                                    <div style={{ fontSize: '12px', color: '#6b7280', marginBottom: '6px', fontWeight: '600' }}>
                                        Submit to Boltz2 vs {targetName}
                                    </div>
                                    {hitSmiles && lockedTooltip.score >= 1 && (
                                        <div style={{ fontSize: '11px', color: '#d97706', marginBottom: '8px' }}>
                                            Known hit is identical to query (TC=1) — hit not submitted.
                                        </div>
                                    )}

                                    {submitBtnLabel && (
                                        <div style={{ marginBottom: '8px' }}>
                                            <button
                                                disabled={submitBtnDisabled}
                                                onClick={() => submitBothFromHeatmap(chemblId, queryName, querySmiles, hitId, hasHit ? hitSmiles : null)}
                                                style={{
                                                    width: '100%',
                                                    padding: '8px 14px',
                                                    backgroundColor: submitBtnDisabled ? '#e5e7eb' : '#0d9488',
                                                    color: submitBtnDisabled ? '#9ca3af' : 'white',
                                                    border: 'none',
                                                    borderRadius: '6px',
                                                    cursor: submitBtnDisabled ? 'not-allowed' : 'pointer',
                                                    fontWeight: '600',
                                                    fontSize: '13px',
                                                }}
                                                title={!altchaVerified ? 'Complete the CAPTCHA below first' : ''}
                                            >
                                                {submitBtnLabel}
                                            </button>
                                        </div>
                                    )}

                                    {statusLines.length > 0 && (
                                        <div style={{ fontSize: '12px', color: '#6b7280', marginBottom: '8px' }}>
                                            {statusLines.map((line, i) => <div key={i}>{line}</div>)}
                                        </div>
                                    )}

                                    {queryStatus === 'failed' && (
                                        <div style={{ marginBottom: '6px' }}>
                                            <button
                                                disabled={!altchaDisabled && !altchaVerified}
                                                onClick={() => submitBoltzFromHeatmap(chemblId, queryName, querySmiles)}
                                                style={retryBtnStyle(!altchaDisabled && !altchaVerified)}
                                            >
                                                Retry Query
                                            </button>
                                        </div>
                                    )}
                                    {hasHit && hitStatus === 'failed' && (
                                        <div style={{ marginBottom: '6px' }}>
                                            <button
                                                disabled={!altchaDisabled && !altchaVerified}
                                                onClick={() => submitBoltzFromHeatmap(chemblId, hitId, hitSmiles)}
                                                style={retryBtnStyle(!altchaDisabled && !altchaVerified)}
                                            >
                                                Retry Hit
                                            </button>
                                        </div>
                                    )}

                                    {bothDone && (
                                        <div style={{ marginBottom: '8px' }}>
                                            <a
                                                href={downloadBothUrl}
                                                style={{ color: '#4f46e5', fontWeight: '600', fontSize: '13px', display: 'block', marginBottom: '6px' }}
                                            >
                                                {hasHit ? 'Download Both Results' : 'Download Results'}
                                            </a>
                                            {hasHit && (
                                                <>
                                                    <a
                                                        href={chimeraxUrl}
                                                        title="Download ZIP, extract, then double-click open_in_chimerax.cxc"
                                                        style={{ color: '#7c3aed', fontWeight: '600', fontSize: '13px', display: 'block' }}
                                                    >
                                                        Open in ChimeraX ↓
                                                    </a>
                                                    <div style={{ fontSize: '11px', color: '#9ca3af', marginTop: '4px' }}>
                                                        Extract ZIP, then double-click open_in_chimerax.cxc
                                                    </div>
                                                </>
                                            )}
                                        </div>
                                    )}

                                    {!bothDone && queryDone && (
                                        <div style={{ marginBottom: '6px' }}>
                                            <a
                                                href={`/api/boltz_download?task_id=${taskId}&chembl_id=${chemblId}&ligand=${encodeURIComponent(queryName)}`}
                                                style={{ color: '#4f46e5', fontSize: '12px' }}
                                            >
                                                Download Query Results
                                            </a>
                                        </div>
                                    )}
                                    {!bothDone && hasHit && hitDone && (
                                        <div style={{ marginBottom: '6px' }}>
                                            <a
                                                href={`/api/boltz_download?task_id=${taskId}&chembl_id=${chemblId}&ligand=${encodeURIComponent(hitId)}`}
                                                style={{ color: '#4f46e5', fontSize: '12px' }}
                                            >
                                                Download Hit Results
                                            </a>
                                        </div>
                                    )}

                                    {submitBtnLabel && altchaDisabled === false && (
                                        <div style={{ marginTop: '10px' }}>
                                            <altcha-widget
                                                ref={altchaCallbackRef}
                                                challengeurl="/api/captcha/challenge"
                                                style={{ display: 'block' }}
                                            />
                                        </div>
                                    )}
                                    {submitBtnLabel && !altchaVerified && altchaDisabled === false && (
                                        <div style={{ fontSize: '11px', color: '#6b7280', marginTop: '4px', textAlign: 'center' }}>
                                            Complete the CAPTCHA first
                                        </div>
                                    )}
                                </div>
                            );
                        })()}

                    </div>{/* end white molecule card */}
                    </div>{/* end sticky wrapper */}

                    {/* Submitted Boltz jobs table */}
                    {isInternal && Object.keys(boltzJobs).length > 0 && (() => {
                        const jobsByTarget = Object.entries(boltzJobs).reduce((acc, [jobKey, info]) => {
                            const target = info.chembl_id || '—';
                            if (!acc[target]) acc[target] = [];
                            acc[target].push([jobKey, info]);
                            return acc;
                        }, {});
                        const statusColors = { pending: '#d97706', running: '#2563eb', done: '#16a34a', failed: '#dc2626', queued: '#6b7280' };
                        return (
                            <div style={{ marginTop: '16px' }}>
                                <div style={{ fontSize: '13px', fontWeight: '600', color: '#374151', marginBottom: '8px' }}>Submitted Jobs</div>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                                    <thead>
                                        <tr style={{ backgroundColor: '#f3f4f6' }}>
                                            <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: '600', color: '#6b7280', borderBottom: '1px solid #e5e7eb' }}>Target</th>
                                            <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: '600', color: '#6b7280', borderBottom: '1px solid #e5e7eb' }}>Ligand</th>
                                            <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: '600', color: '#6b7280', borderBottom: '1px solid #e5e7eb' }}>Task ID</th>
                                            <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: '600', color: '#6b7280', borderBottom: '1px solid #e5e7eb' }}>Status</th>
                                            <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: '600', color: '#6b7280', borderBottom: '1px solid #e5e7eb' }}></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {Object.entries(jobsByTarget).map(([target, jobs]) => (
                                            jobs.map(([jobKey, info], i) => {
                                                const color = statusColors[info.status] || '#6b7280';
                                                const isLast = i === jobs.length - 1;
                                                return (
                                                    <tr key={jobKey} style={{ backgroundColor: i % 2 === 0 ? 'white' : '#f9fafb' }} onMouseEnter={() => handleJobRowHover(info.chembl_id, info.ligand)} onMouseLeave={handleJobRowLeave}>
                                                        {i === 0 && (
                                                            <td rowSpan={jobs.length} style={{ padding: '6px 10px', borderBottom: '1px solid #e5e7eb', borderRight: '1px solid #e5e7eb', color: '#374151', fontWeight: '600', verticalAlign: 'middle' }}>
                                                                {target}
                                                            </td>
                                                        )}
                                                        <td style={{ padding: '6px 10px', borderBottom: isLast ? '2px solid #d1d5db' : '1px solid #e5e7eb', color: '#374151' }}>{info.ligand || '—'}</td>
                                                        <td style={{ padding: '6px 10px', borderBottom: isLast ? '2px solid #d1d5db' : '1px solid #e5e7eb', color: '#6b7280', fontFamily: 'monospace' }}>{info.task_id || '—'}</td>
                                                        <td style={{ padding: '6px 10px', borderBottom: isLast ? '2px solid #d1d5db' : '1px solid #e5e7eb' }}>
                                                            <span style={{ color, fontWeight: '500' }}>{info.status}</span>
                                                        </td>
                                                        <td style={{ padding: '6px 10px', borderBottom: isLast ? '2px solid #d1d5db' : '1px solid #e5e7eb' }}>
                                                            {info.status === 'done' && (
                                                                <a
                                                                    href={`/api/boltz_download?task_id=${taskId}&chembl_id=${info.chembl_id}${info.ligand ? `&ligand=${encodeURIComponent(info.ligand)}` : ''}`}
                                                                    style={{ color: '#6366f1', textDecoration: 'underline' }}
                                                                >
                                                                    Download
                                                                </a>
                                                            )}
                                                        </td>
                                                    </tr>
                                                );
                                            })
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        );
                    })()}

                </div>{/* end right column */}
            </div>{/* end main two-column row */}
        </Layout>
    )
}



