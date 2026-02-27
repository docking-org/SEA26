import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import Layout from './components/Layout';


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

    const handleCompoundChange = (e) => {
        setSelectedCompoundIndex(Number(e.target.value));
    };

    const handleMouseEnter = (e, target, hit) => {
        if (!hit) return;
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

    // Calculate dynamic cell size based on number of hits
    const cellSize = useMemo(() => {
        if (!tableData) return { width: 30, height: 25 };
        // Aim for table to fill roughly half the screen width (minus some padding)
        // Assume ~800px for half screen, subtract ~150px for target labels
        const availableWidth = 650;
        const calculatedWidth = Math.max(20, Math.min(50, Math.floor(availableWidth / tableData.maxHits)));
        return { width: calculatedWidth, height: Math.max(25, calculatedWidth) };
    }, [tableData]);

    return (
        <Layout>
            <h1 className="text-2xl font-bold">Compound vs Target Heatmap</h1>
            <br />
            <p>Task ID: {taskId}</p>
            
            {/* Compound Selector Dropdown */}
            {allCompounds.length > 0 && (
                <div style={{ marginBottom: '20px' }}>
                    <label htmlFor="compound-select" style={{ marginRight: '10px', fontWeight: 'bold' }}>
                        Select Compound:
                    </label>
                    <select 
                        id="compound-select"
                        value={selectedCompoundIndex}
                        onChange={handleCompoundChange}
                        style={{
                            padding: '8px 12px',
                            fontSize: '14px',
                            borderRadius: '4px',
                            border: '1px solid #ccc',
                            minWidth: '250px',
                            cursor: 'pointer'
                        }}
                    >
                        {allCompounds.map((compound, index) => (
                            <option key={index} value={index}>
                                {compound.Name} ({Object.keys(compound.hits || {}).length} targets)
                            </option>
                        ))}
                    </select>
                    <span style={{ marginLeft: '15px', color: '#666', fontSize: '13px' }}>
                        {allCompounds.length} compound{allCompounds.length !== 1 ? 's' : ''} available
                    </span>
                </div>
            )}

            {heatmapData && (
                <p><strong>Compound:</strong> {heatmapData.Name} | <strong>SMILES:</strong> {heatmapData.SMILES}</p>
            )}
            {loading && <p>Loading...</p>}
            {error && <p style={{ color: 'red' }}>{error}</p>}
            {!rdkitReady && <p style={{ color: '#666' }}>Loading RDKit...</p>}
            
            {/* Main content area with table and tooltip 50:50 */}
            <div style={{ 
                display: 'flex', 
                gap: '20px', 
                marginTop: '20px', 
                alignItems: 'flex-start',
                width: '100%'
            }}>
                {/* Heatmap table - 50% */}
                <div style={{ flex: '1 1 50%', minWidth: 0, overflow: 'auto' }}>
                    {tableData && (
                        <div style={{ display: 'flex' }}>
                            <table style={{ 
                                borderCollapse: 'collapse', 
                                fontSize: '11px'
                            }}>
                                <tbody>
                                    {tableData.targets.map(target => (
                                        <tr key={target}>
                                            {Array.from({ length: tableData.maxHits }).map((_, index) => {
                                                const hit = tableData.hitsPerTarget[target][index];
                                                return (
                                                    <td 
                                                        key={index}
                                                        onMouseEnter={(e) => handleMouseEnter(e, target, hit)}
                                                        onMouseLeave={handleMouseLeave}
                                                        style={{ 
                                                            backgroundColor: getColorForScore(hit?.score, hit, target),
                                                            minWidth: `${cellSize.width}px`,
                                                            width: `${cellSize.width}px`,
                                                            height: `${cellSize.height}px`,
                                                            padding: 0,
                                                            cursor: hit ? 'pointer' : 'default',
                                                            border: '1px solid #eee'
                                                        }}
                                                    />
                                                );
                                            })}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            <div style={{ marginLeft: '10px', fontSize: '12px', flexShrink: 0 }}>
                                {tableData.targets.map(target => (
                                    <div key={target} style={{ 
                                        height: `${cellSize.height}px`, 
                                        lineHeight: `${cellSize.height}px`, 
                                        whiteSpace: 'nowrap',
                                        borderBottom: '1px solid transparent'
                                    }}>
                                        {target}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Tooltip with Molecule Images - 50% */}
                <div style={{ 
                    flex: '1 1 50%', 
                    minWidth: 0,
                    minHeight: '300px'
                }}>
                    {tooltip.visible && rdkitReady ? (
                        <div style={{
                            backgroundColor: 'white',
                            border: '1px solid #ccc',
                            color: '#333',
                            borderRadius: '8px',
                            padding: '20px',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                            height: '100%'
                        }}>
                            <div style={{ marginBottom: '15px' }}>
                                <strong>Target:</strong> {tooltip.target}<br />
                                <strong>Known Hit ID:</strong> {tooltip.hitId}<br />
                                <strong>Tanimoto Score:</strong> {tooltip.score.toFixed(4)}
                            </div>
                            
                            <div style={{ display: 'flex', gap: '20px', justifyContent: 'center' }}>
                                {/* Query Compound */}
                                <div style={{ flex: 1, textAlign: 'center', maxWidth: '250px' }}>
                                    <div style={{ fontWeight: 'bold', marginBottom: '8px', fontSize: '13px' }}>
                                        Query Compound
                                    </div>
                                    <div style={{ fontSize: '11px', color: '#666', marginBottom: '8px' }}>
                                        {heatmapData.Name}
                                    </div>
                                    <MoleculeSVG smiles={heatmapData.SMILES} width={220} height={180} />
                                    <div style={{ fontSize: '10px', color: '#888', marginTop: '8px', wordBreak: 'break-all' }}>
                                        {heatmapData.SMILES.length > 40 
                                            ? heatmapData.SMILES
                                            : heatmapData.SMILES}
                                    </div>
                                </div>

                                {/* Known Hit */}
                                <div style={{ flex: 1, textAlign: 'center', maxWidth: '250px' }}>
                                    <div style={{ fontWeight: 'bold', marginBottom: '8px', fontSize: '13px' }}>
                                        Known Hit
                                    </div>
                                    <div style={{ fontSize: '11px', color: '#666', marginBottom: '8px' }}>
                                        {tooltip.hitId}
                                    </div>
                                    <MoleculeSVG smiles={tooltip.hitSmiles} width={220} height={180} />
                                    <div style={{ fontSize: '10px', color: '#888', marginTop: '8px', wordBreak: 'break-all' }}>
                                        {tooltip.hitSmiles.length > 40 
                                            ? tooltip.hitSmiles
                                            : tooltip.hitSmiles}
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
                            height: '100%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            color: '#999',
                            fontSize: '14px'
                        }}>
                            Hover over a cell to see molecule details
                        </div>
                    )}
                </div>
            </div>
        </Layout>
    )
}



