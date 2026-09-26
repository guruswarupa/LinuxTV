import QtQuick 2.15

// A single home-screen tile: a solid brand-color banner with a centered
// icon (or a banner image, when the app config provides one), a name label
// that fades in on focus, and an Android-TV-style zoom + glow when active.
Item {
    id: root

    property string appName: ""
    property string appKey: ""
    property string cardColor: "#334155"
    property string iconSource: ""
    property string bannerSource: ""
    property bool active: false
    property bool reduced: false
    property bool isAdd: false
    property bool dragEnabled: true
    property real cardRadius: 14

    signal activated()
    signal contextRequested()
    signal dragStarted(string key)
    signal dragMoved(real dx)
    signal dragEnded()

    readonly property real focusScale: reduced ? 1.05 : 1.1
    readonly property int animDuration: reduced ? 90 : 150
    property bool isDragSource: false

    opacity: isDragSource ? 0.35 : 1.0
    Behavior on opacity { NumberAnimation { duration: 120 } }

    scale: active ? focusScale : 1.0
    // Grow only downward on focus, so the zoom never reaches up into the
    // row title above -- pin the pivot to the top instead of the center.
    transformOrigin: Item.Top
    z: active ? 20 : 1
    Behavior on scale {
        NumberAnimation { duration: root.animDuration; easing.type: Easing.OutCubic }
    }

    // Soft halo behind the card, visible only while focused.
    Rectangle {
        anchors.fill: art
        anchors.margins: -8
        radius: root.cardRadius + 6
        color: "transparent"
        border.width: 8
        border.color: Qt.rgba(1, 1, 1, 0.25)
        opacity: root.active ? 1 : 0
        visible: !root.reduced
        Behavior on opacity { NumberAnimation { duration: root.animDuration } }
    }

    Rectangle {
        id: art
        width: parent.width
        height: parent.height * 0.76
        radius: root.bannerSource === "" ? root.cardRadius : 0
        color: root.cardColor
        clip: true
        border.width: root.active ? 3 : 0
        border.color: "white"
        Behavior on border.width { NumberAnimation { duration: root.animDuration } }

        Image {
            anchors.fill: parent
            visible: root.bannerSource !== ""
            source: root.bannerSource
            fillMode: Image.PreserveAspectCrop
            // Asynchronous loading decodes on a background thread and
            // uploads the result to the GPU separately from the main
            // render pass -- on some platforms (notably virtualized/
            // translated GPU stacks like WSLg) that upload can silently
            // fail for specific textures. These icons are already tiny,
            // pre-normalized PNGs, so loading them synchronously costs
            // nothing and sidesteps that path entirely.
            asynchronous: false
            smooth: true
        }

        // A white squircle plate behind the icon/letter -- every brand
        // mark, whatever its own native color or contrast, reads clearly
        // against a fixed white backdrop instead of directly on the
        // (widely varying) card color.
        Rectangle {
            id: iconBadge
            anchors.centerIn: parent
            visible: !root.isAdd && root.bannerSource === ""
            width: parent.height * 0.62
            height: width
            radius: width * 0.26
            color: "white"
        }

        Image {
            id: iconImage
            anchors.centerIn: parent
            visible: !root.isAdd && root.bannerSource === "" && root.iconSource !== ""
            source: root.iconSource
            width: iconBadge.width * 0.9
            height: width
            fillMode: Image.PreserveAspectFit
            asynchronous: false
            smooth: true
            // Every card in every row loads from the same normalized-icon
            // cache directory, so many Image elements can end up sharing
            // Qt Quick's process-wide pixmap cache entry for the same URL.
            // If any one of them ever hit that URL before the file was
            // ready, the cached failure can stick for everyone reading it
            // afterward -- disable sharing so each card always does its
            // own independent load straight from disk.
            cache: false
        }

        Text {
            anchors.centerIn: parent
            visible: !root.isAdd && root.bannerSource === "" && root.iconSource === ""
            text: root.appName.length > 0 ? root.appName.charAt(0).toUpperCase() : "?"
            color: root.cardColor
            font.pixelSize: parent.height * 0.28
            font.bold: true
        }

        Text {
            anchors.centerIn: parent
            visible: root.isAdd
            text: "+"
            color: "white"
            font.pixelSize: parent.height * 0.5
            font.bold: true
        }
    }

    Text {
        anchors.top: art.bottom
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.right: parent.right
        horizontalAlignment: Text.AlignHCenter
        text: root.appName
        color: "white"
        font.pixelSize: 15
        font.bold: true
        elide: Text.ElideRight
        opacity: root.active ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: root.animDuration } }
    }

    MouseArea {
        id: cardMouse
        anchors.fill: art
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        property real pressX: 0
        property bool dragging: false

        onPressed: (mouse) => {
            pressX = mouse.x
            dragging = false
        }
        onPositionChanged: (mouse) => {
            if ((mouse.buttons & Qt.LeftButton) && root.dragEnabled && !root.isAdd) {
                var dx = mouse.x - pressX
                if (!dragging && Math.abs(dx) > 14) {
                    dragging = true
                    root.dragStarted(root.appKey)
                }
                if (dragging) {
                    root.dragMoved(mouse.x - pressX)
                }
            }
        }
        onReleased: {
            if (dragging) {
                root.dragEnded()
            }
        }
        onClicked: (mouse) => {
            if (dragging) {
                dragging = false
                return
            }
            if (mouse.button === Qt.RightButton) {
                root.contextRequested()
            } else {
                root.activated()
            }
        }
    }
}
