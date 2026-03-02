import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import Layout from './components/Layout';

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

    const { width: windowWidth } = useWindowSize();
    
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

    const getColorForScore = (score, hit, target) => {
        if (score === undefined) return '#ffffff';
        const intensity = Math.min(score, 1);
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

    // Clear lock when compound changes
    const handleCompoundChange = (e) => {
        setSelectedCompoundIndex(Number(e.target.value));
        setLockedTooltip(null);
        setTooltip(prev => ({ ...prev, visible: false }));
    };

    useEffect(() => {
        if (taskId) {
            fetchHeatMapData();
        }
    }, [taskId]);

    useEffect(() => {
        console.log("heatmapData updated:", heatmapData);
    }, [heatmapData]);

    // Calculate dynamic cell size based on screen size and number of hits
    const cellSize = useMemo(() => {
        if (!tableData) return { width: 30, height: 25 };
        
        let availableWidth;
        if (isMobile) {
            availableWidth = windowWidth - 120; // Account for target labels and padding
        } else if (isTablet) {
            availableWidth = windowWidth * 0.5 - 100;
        } else {
            availableWidth = 650;
        }
        
        const calculatedWidth = Math.max(
            isMobile ? 15 : 20, 
            Math.min(isMobile ? 30 : 50, Math.floor(availableWidth / tableData.maxHits))
        );
        return { 
            width: calculatedWidth, 
            height: Math.max(isMobile ? 20 : 25, calculatedWidth) 
        };
    }, [tableData, isMobile, isTablet, windowWidth]);

    // Responsive molecule image sizes
    const moleculeSize = useMemo(() => {
        if (isMobile) {
            return { width: 150, height: 120 };
        } else if (isTablet) {
            return { width: 180, height: 150 };
        }
        return { width: 220, height: 180 };
    }, [isMobile, isTablet]);

    // Determine which tooltip data to display (locked takes priority)
    const displayTooltip = lockedTooltip || tooltip;
    const showTooltip = lockedTooltip ? true : tooltip.visible;

    return (
        <Layout>
            <h1 className={`font-bold ${isMobile ? 'text-xl' : 'text-2xl'}`}>
                Compound vs Target Heatmap
            </h1>
            <br />
            <p className={isMobile ? 'text-sm' : ''}>Task ID: {taskId}</p>
            
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
                        color: '#666', 
                        fontSize: isMobile ? '12px' : '13px' 
                    }}>
                        {allCompounds.length} compound{allCompounds.length !== 1 ? 's' : ''} available
                    </span>
                </div>
            )}

            {heatmapData && (
                <p style={{ 
                    fontSize: isMobile ? '12px' : '14px',
                    wordBreak: 'break-all'
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
                alignItems: 'flex-start',
                width: '100%'
            }}>
                {/* Heatmap table */}
                <div style={{ 
                    flex: isDesktop ? '1 1 50%' : '1 1 100%', 
                    minWidth: 0, 
                    overflow: 'auto',
                    maxWidth: '100%'
                }}>
                    {tableData && (
                        <div style={{ 
                            display: 'flex',
                            overflowX: 'auto',
                            maxWidth: '100%'
                        }}>
                            <table style={{ 
                                borderCollapse: 'collapse', 
                                fontSize: isMobile ? '10px' : '11px'
                            }}>
                                <tbody>
                                    {tableData.targets.map(target => (
                                        <tr key={target}>
                                            {Array.from({ length: tableData.maxHits }).map((_, index) => {
                                                const hit = tableData.hitsPerTarget[target][index];
                                                const isLocked = lockedTooltip && 
                                                    lockedTooltip.hitId === hit?.id && 
                                                    lockedTooltip.target === target;
                                                return (
                                                    <td 
                                                        key={index}
                                                        onMouseEnter={isDesktop ? (e) => handleMouseEnter(e, target, hit) : undefined}
                                                        onMouseLeave={isDesktop ? handleMouseLeave : undefined}
                                                        onClick={(e) => handleCellClick(e, target, hit)}
                                                        style={{ 
                                                            backgroundColor: getColorForScore(hit?.score, hit, target),
                                                            minWidth: `${cellSize.width}px`,
                                                            width: `${cellSize.width}px`,
                                                            height: `${cellSize.height}px`,
                                                            padding: 0,
                                                            cursor: hit ? 'pointer' : 'default',
                                                            border: isLocked 
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
                                        borderBottom: '1px solid transparent',
                                        maxWidth: isMobile ? '80px' : 'none',
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis'
                                    }}>
                                        {target}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Tooltip with Molecule Images */}
                <div style={{ 
                    flex: isDesktop ? '1 1 50%' : '1 1 100%', 
                    minWidth: 0,
                    minHeight: isMobile ? '250px' : '300px',
                    width: '100%'
                }}>
                    {showTooltip && rdkitReady ? (
                        <div style={{
                            backgroundColor: 'white',
                            border: lockedTooltip ? '2px solid #007bff' : '1px solid #ccc',
                            color: '#333',
                            borderRadius: '8px',
                            padding: isMobile ? '15px' : '20px',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                            width: '100%'
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
                                        📌 Click the same cell or "Unlock" to release
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
                            
                            <div style={{ 
                                marginBottom: '15px',
                                fontSize: isMobile ? '13px' : '14px'
                            }}>
                                <strong>Target:</strong> {displayTooltip.target}<br />
                                <strong>Known Hit ID:</strong> {displayTooltip.hitId}<br />
                                <strong>Tanimoto Score:</strong> {displayTooltip.score.toFixed(4)}
                            </div>
                            
                            <div style={{ 
                                display: 'flex', 
                                flexDirection: isMobile ? 'column' : 'row',
                                gap: isMobile ? '15px' : '20px', 
                                justifyContent: 'center',
                                alignItems: 'center'
                            }}>
                                {/* Query Compound */}
                                <div style={{ 
                                    flex: 1, 
                                    textAlign: 'center', 
                                    maxWidth: isMobile ? '100%' : '250px',
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

                                {/* Known Hit */}
                                <div style={{ 
                                    flex: 1, 
                                    textAlign: 'center', 
                                    maxWidth: isMobile ? '100%' : '250px',
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
                            </div>
                        </div>
                    ) : (
                        <div style={{
                            backgroundColor: '#f9f9f9',
                            border: '1px dashed #ccc',
                            borderRadius: '8px',
                            padding: '20px',
                            minHeight: isMobile ? '100px' : '200px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            color: '#999',
                            fontSize: isMobile ? '13px' : '14px'
                        }}>
                            {isDesktop 
                                ? 'Hover to preview, click to pin molecule details'
                                : 'Tap on a cell to see molecule details'
                            }
                        </div>
                    )}
                </div>
            </div>
        </Layout>
    )
}



