import QtQuick 2.15

Column {
    id: root

    property string title: ""
    property var apps: []
    property bool rowActive: false
    property int activeCol: 0

    property int cardWidth: 280
    property int cardHeight: 190

    signal cardActivated(int col)
    signal cardContext(int col)

    // Mouse-driven drag-to-reorder state. Dragging is horizontal only and
    // only ever reorders within this same row (same underlying collection),
    // matching what the context menu's Move Left/Right already allow.
    property string dragKey: ""
    property int dragSourceIndex: -1
    property real dragBaseX: 0
    property real dragDeltaX: 0
    property color dragColor: theme.accent
    property string dragIcon: ""

    function indexForKey(key) {
        for (var i = 0; i < root.apps.length; i++) {
            if (root.apps[i].key === key) return i
        }
        return -1
    }

    function slotX(i) {
        return list.leftMargin + i * (root.cardWidth + list.spacing) - list.contentX
    }

    function beginDrag(key) {
        var i = root.indexForKey(key)
        if (i < 0) return
        root.dragKey = key
        root.dragSourceIndex = i
        root.dragBaseX = root.slotX(i)
        root.dragDeltaX = 0
        var art = backend.art[key]
        root.dragColor = art ? art.color : theme.accent
        root.dragIcon = (art && art.icon) ? ("file://" + art.icon) : ""
    }

    function updateDrag(dx) {
        root.dragDeltaX = dx
    }

    function endDrag() {
        if (root.dragKey === "") return
        var slotWidth = root.cardWidth + list.spacing
        var targetIndex = Math.round((root.dragBaseX + root.dragDeltaX - list.leftMargin + list.contentX) / slotWidth)
        targetIndex = Math.max(0, Math.min(targetIndex, root.apps.length - 1))
        var targetKey = root.apps[targetIndex] ? root.apps[targetIndex].key : ""
        var sourceKey = root.dragKey
        root.dragKey = ""
        root.dragSourceIndex = -1
        root.dragDeltaX = 0
        if (targetKey !== "" && targetKey !== sourceKey) {
            backend.reorderDrag(sourceKey, targetKey)
        }
    }

    // AppCard zooms from its top edge (transformOrigin: Item.Top), so the
    // card never grows upward -- this gap only needs to clear the halo's
    // fixed 8px overhang, not the whole scale-up. It's real Column spacing,
    // not a ListView margin, so it reliably renders instead of silently
    // doing nothing (ListView top/bottom margins don't shift a horizontal
    // orientation's delegates -- they stay top-aligned regardless).
    spacing: 20

    Text {
        text: root.title
        color: theme.text
        font.family: theme.font_family
        font.pixelSize: 22
        font.bold: true
        leftPadding: 24
    }

    Item {
        id: rowContent
        width: root.width
        // A little slack below for the zoomed card's downward growth and
        // the focus-only name label -- not clipped, so it never crops it.
        height: root.cardHeight + 26

        ListView {
            id: list
            anchors.fill: parent
            orientation: ListView.Horizontal
            model: root.apps
            spacing: 34
            leftMargin: 24
            rightMargin: 24
            interactive: false
            highlightRangeMode: ListView.StrictlyEnforceRange
            preferredHighlightBegin: 24
            preferredHighlightEnd: 24 + root.cardWidth
            highlightMoveDuration: root.rowActive && backend.reducedEffects ? 90 : 180

            currentIndex: root.rowActive ? root.activeCol : -1

            delegate: AppCard {
                width: root.cardWidth
                height: root.cardHeight
                appName: modelData.name
                appKey: modelData.key
                isAdd: modelData.isAdd === true
                dragEnabled: !modelData.isAdd
                isDragSource: root.dragKey !== "" && root.dragKey === modelData.key
                cardColor: (!modelData.isAdd && backend.art[modelData.key]) ? backend.art[modelData.key].color : theme.accent
                iconSource: (!modelData.isAdd && backend.art[modelData.key] && backend.art[modelData.key].icon)
                            ? ("file://" + backend.art[modelData.key].icon) : ""
                bannerSource: (!modelData.isAdd && modelData.banner) ? ("file://" + modelData.banner) : ""
                active: root.rowActive && index === root.activeCol
                reduced: backend.reducedEffects
                onActivated: root.cardActivated(index)
                onContextRequested: root.cardContext(index)
                onDragStarted: (key) => root.beginDrag(key)
                onDragMoved: (dx) => root.updateDrag(dx)
                onDragEnded: root.endDrag()
            }
        }

        // Floating preview of the card being dragged, following the mouse
        // horizontally so there's clear visual feedback for where it'll land.
        Rectangle {
            id: dragGhost
            visible: root.dragKey !== ""
            width: root.cardWidth
            height: root.cardHeight * 0.76
            x: root.dragBaseX + root.dragDeltaX
            y: 0
            radius: 14
            color: root.dragColor
            opacity: 0.9
            border.width: 3
            border.color: "white"
            z: 100

            Rectangle {
                id: dragIconBadge
                anchors.centerIn: parent
                visible: root.dragIcon !== ""
                width: parent.height * 0.62
                height: width
                radius: width * 0.26
                color: "white"
            }

            Image {
                anchors.centerIn: parent
                visible: root.dragIcon !== ""
                source: root.dragIcon
                width: dragIconBadge.width * 0.9
                height: width
                fillMode: Image.PreserveAspectFit
                asynchronous: false
                smooth: true
                cache: false
            }
        }
    }
}
